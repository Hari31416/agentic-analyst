"""Reindex persisted chunks without changing extraction or saved citation locations."""

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.models import Document, Job, now
from app.db.session import get_session

router = APIRouter()
Db = Annotated[Session, Depends(get_session)]


@router.post("/api/documents/{document_id}/reindex", status_code=202)
def queue_reindex(document_id: str, session: Db) -> dict[str, Any]:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "Document not found")
    if document.state != "ready":
        raise HTTPException(409, "Finish document extraction before reindexing")
    settings = get_settings()
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "document_id": document_id,
                "model": settings.embedding_model,
                "revision": settings.embedding_revision,
                "dimensions": settings.embedding_dimension,
                "chunker": document.chunker_version,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    key = "index:" + fingerprint
    job = session.scalar(select(Job).where(Job.dedupe_key == key))
    if job is None:
        job = Job(
            kind="index_document", payload={"document_id": document_id}, dedupe_key=key
        )
        session.add(job)
        session.commit()
    if job.state in {"failed", "completed"} and document.stage == "index_degraded":
        job.state = "queued"
        job.attempts = 0
        job.result = None
        job.available_at = now()
        job.lease_owner = None
        job.lease_token = None
        job.lease_expires_at = None
        session.commit()
    return {
        "document_id": document_id,
        "job_id": job.id,
        "state": job.state,
        "index_generation_id": document.index_generation_id,
    }


def process_index(
    document_id: str, settings: Settings, dbfactory: sessionmaker[Session], guard: Any
) -> dict[str, object]:
    from app.retrieval.service import build_index_generation

    with dbfactory() as session:
        document = session.get(Document, document_id)
        if document is None or document.state != "ready":
            raise ValueError("document extraction is not ready")
        guard(session)
        # Release the job row during local inference so heartbeat can extend it.
        session.commit()
        generation = build_index_generation(
            session, document_id, settings, lease_guard=guard
        )
        guard(session)
        document.stage = "indexed" if generation.status == "ready" else "index_degraded"
        document.details = {**document.details, "index_status": generation.status}
        session.commit()
        return {
            "document_id": document_id,
            "generation_id": generation.id,
            "index_status": generation.status,
        }
