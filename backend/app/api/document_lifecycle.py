"""Retry durable ingestion and archive sources without changing saved citations."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Document, Job, Source, now
from app.db.session import get_session

router = APIRouter(tags=["documents"])
Db = Annotated[Session, Depends(get_session)]


def locked_document(session: Session, document_id: str) -> tuple[Document, Source]:
    document = session.scalar(
        select(Document).where(Document.id == document_id).with_for_update()
    )
    if document is None:
        raise HTTPException(404, "Document not found")
    source = session.get(Source, document.source_id)
    if source is None:
        raise HTTPException(404, "Document source not found")
    return document, source


@router.post("/api/documents/{document_id}/retry", status_code=202)
def retry_document(document_id: UUID, session: Db) -> dict[str, Any]:
    jobs = list(
        session.scalars(
            select(Job)
            .where(
                Job.kind == "ingest_document",
                Job.payload["document_id"].as_string() == str(document_id),
            )
            .with_for_update()
        )
    )
    document, source = locked_document(session, str(document_id))
    if source.state == "deleted":
        raise HTTPException(410, "This document has been removed")
    active = next((job for job in jobs if job.state in {"queued", "running"}), None)
    if active:
        return {"document_id": document.id, "job_id": active.id, "state": active.state}
    if document.state not in {"failed", "ocr_needed"}:
        raise HTTPException(409, "Only failed or OCR-needed extraction can be retried")
    job = (
        jobs[0]
        if jobs
        else Job(
            kind="ingest_document",
            payload={"document_id": document.id},
            dedupe_key=f"ingest_document:retry:{document.id}",
        )
    )
    session.add(job)
    job.state = "queued"
    job.attempts = 0
    job.result = None
    job.available_at = now()
    job.lease_owner = job.lease_token = None
    job.lease_expires_at = None
    document.state = document.stage = "queued"
    document.progress = 0
    document.details = {
        key: value for key, value in document.details.items() if key != "error"
    }
    source.state = "processing"
    session.commit()
    return {"document_id": document.id, "job_id": job.id, "state": job.state}


@router.delete("/api/documents/{document_id}")
def remove_document(document_id: UUID, session: Db) -> dict[str, Any]:
    jobs = list(
        session.scalars(
            select(Job)
            .where(
                Job.kind.in_(["ingest_document", "index_document"]),
                Job.payload["document_id"].as_string() == str(document_id),
            )
            .with_for_update()
        )
    )
    document, source = locked_document(session, str(document_id))
    for job in jobs:
        if job.state in {"queued", "running"}:
            job.state = "cancelled"
            job.lease_token = job.lease_owner = None
            job.lease_expires_at = None
    source.state = document.state = "deleted"
    document.stage = "removed"
    document.details = {
        **document.details,
        "removed_at": now().isoformat(),
        "retention": "archived_for_citations",
    }
    session.commit()
    return {
        "document_id": document.id,
        "state": "deleted",
        "retention": "archived_for_citations",
    }
