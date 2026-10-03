"""Preview extracted document tables and explicitly accept them for analysis."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_session
from app.sources.document_tables import (
    DocumentTableError,
    accept_document_table,
    get_document_tables,
)
from app.storage.factory import get_storage

router = APIRouter(tags=["documents"])
Db = Annotated[Session, Depends(get_session)]


def _table_error(error: DocumentTableError) -> HTTPException:
    status = {
        "document_not_ready": 409,
        "table_not_found": 404,
        "source_not_found": 404,
        "table_cells_unavailable": 409,
        "table_empty": 409,
        "table_too_large": 413,
    }.get(error.code, 400)
    return HTTPException(
        status_code=status, detail={"code": error.code, "message": str(error)}
    )


@router.get("/api/documents/{document_id}/tables")
def document_tables(document_id: UUID, session: Db) -> dict[str, Any]:
    result = get_document_tables(session, str(document_id))
    if result is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return result


@router.post("/api/documents/{document_id}/tables/{table_id}/accept")
def accept_table(document_id: UUID, table_id: str, session: Db) -> dict[str, Any]:
    from sqlalchemy import select
    from app.db.models import Document
    from app.api.resource_lifecycle import lock_source_workspace

    source_id = session.scalar(
        select(Document.source_id).where(Document.id == str(document_id))
    )
    if source_id is None:
        raise HTTPException(404, "Document not found")
    lock_source_workspace(session, source_id)
    try:
        result = accept_document_table(
            session,
            get_storage(get_settings()),
            str(document_id),
            table_id,
        )
    except DocumentTableError as error:
        raise _table_error(error) from error
    if result is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return result
