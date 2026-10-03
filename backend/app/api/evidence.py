"""Resolve saved citations from immutable evidence details and document versions."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import Document, Evidence, Source
from app.db.session import get_session

router = APIRouter()
Db = Annotated[Session, Depends(get_session)]


@router.get("/api/evidence/{evidence_id}")
def get_evidence(evidence_id: str, session: Db) -> dict[str, Any]:
    evidence = session.get(Evidence, evidence_id)
    if evidence is None:
        raise HTTPException(404, "Evidence not found")
    details = evidence.details
    display_name = None
    source_state = None
    if evidence.kind == "document":
        document = session.get(Document, details.get("document_id"))
        if document is None or document.source_version != details.get("source_version"):
            raise HTTPException(409, "The citation document version is unavailable")
        source = session.get(Source, document.source_id)
        display_name = source.display_name if source else None
        source_state = (
            "unavailable"
            if source is None
            else "archived" if source.state in {"deleted", "archived"} else "active"
        )
    availability = {}
    for identity in evidence.source_ids:
        origin = session.get(Source, identity)
        expected = details.get("source_versions", {}).get(identity)
        availability[identity] = {
            "state": "unavailable" if origin is None else origin.state,
            "expected_version": expected,
            "current_version": origin.version if origin else None,
            "version_available": origin is not None
            and (expected is None or origin.version == expected),
        }
    return {
        "id": evidence.id,
        "kind": evidence.kind,
        "run_id": evidence.run_id,
        "source_ids": evidence.source_ids,
        "details": details,
        **{
            key: details.get(key)
            for key in (
                "document_id",
                "excerpt",
                "location",
                "context",
                "score",
                "rank",
                "retrieval_mode",
                "trace",
                "chunk_id",
                "generation_id",
            )
        },
        "document_version": details.get("source_version"),
        "display_name": display_name,
        "source_state": source_state,
        "source_availability": availability,
    }
