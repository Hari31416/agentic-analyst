"""Upload and inspect versioned PDF/DOCX documents."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Document, Job, Source, Workspace
from app.db.session import get_session
from app.sources.documents import (
    CHUNKER_VERSION,
    EXTRACTOR_VERSION,
    DocumentIngestionError,
    get_document_blocks,
    list_documents,
    validate_document_upload,
)
from app.storage.factory import get_storage

router = APIRouter(tags=["documents"])
Db = Annotated[Session, Depends(get_session)]
MAX_BLOCK_RESPONSE_BYTES = 1024 * 1024


def _source_view(source: Source) -> dict[str, Any]:
    return {
        "id": source.id,
        "display_name": source.display_name,
        "kind": source.kind,
        "state": source.state,
        "version": source.version,
        "content_hash": source.content_hash,
        "description": source.details.get("description"),
        "metric_hints": source.details.get("metric_hints", {}),
    }


def _document_view(document: Document, source: Source) -> dict[str, Any]:
    return {
        "id": document.id,
        "source_id": document.source_id,
        "source_version": document.source_version,
        "display_name": source.display_name,
        "media_type": source.details.get("media_type"),
        "state": document.state,
        "stage": document.stage,
        "progress": document.progress,
        "extractor_version": document.extractor_version,
        "chunker_version": document.chunker_version,
        "index_generation_id": document.index_generation_id,
        "details": document.details,
        "created_at": document.created_at,
    }


def _upload_error(error: DocumentIngestionError) -> HTTPException:
    if error.code == "upload_too_large":
        status = 413
    elif error.code in {
        "file_type_unsupported",
        "file_type_mismatch",
        "macros_rejected",
    }:
        status = 415
    else:
        status = 400
    return HTTPException(
        status_code=status,
        detail={"code": error.code, "message": str(error)},
    )


@router.post("/api/workspaces/{workspace_id}/documents", status_code=202)
async def upload_document(
    workspace_id: UUID,
    file: Annotated[UploadFile, File()],
    session: Db,
) -> dict[str, Any]:
    settings = get_settings()
    workspace = session.get(Workspace, str(workspace_id))
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    filename = (file.filename or "document").replace("\\", "/").rsplit("/", 1)[-1]
    content = bytearray()
    try:
        while True:
            block = await file.read(
                min(64 * 1024, settings.max_upload_bytes + 1 - len(content))
            )
            if not block:
                break
            content.extend(block)
            if len(content) > settings.max_upload_bytes:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "upload_too_large",
                        "message": "Document exceeds the configured upload limit.",
                    },
                )
        try:
            kind = validate_document_upload(
                filename, bytes(content), settings.max_upload_bytes
            )
        except DocumentIngestionError as error:
            raise _upload_error(error) from error
        media_type = (
            "application/pdf"
            if kind == "pdf"
            else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        digest = hashlib.sha256(content).hexdigest()
        # Serialize same-workspace duplicate checks with the Workspace row lock.
        locked_workspace = session.scalar(
            select(Workspace).where(Workspace.id == str(workspace_id)).with_for_update()
        )
        if locked_workspace is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        existing = session.execute(
            select(Source, Document)
            .join(Document, Document.source_id == Source.id)
            .where(
                Source.workspace_id == str(workspace_id),
                Source.kind == kind,
                Source.content_hash == digest,
                Document.source_version == Source.version,
                Document.extractor_version == EXTRACTOR_VERSION,
                Document.chunker_version == CHUNKER_VERSION,
            )
            .order_by(Document.created_at.desc(), Document.id)
            .limit(1)
        ).first()
        if existing is not None:
            existing_source, existing_document = existing
            return {
                "source": _source_view(existing_source),
                "document": _document_view(existing_document, existing_source),
            }
        source = Source(
            workspace_id=str(workspace_id),
            kind=kind,
            version=1,
            display_name=filename[:255],
            state="processing",
            content_hash=digest,
            details={"media_type": media_type},
        )
        session.add(source)
        session.flush()
        key = f"originals/{workspace_id}/{source.id}/original.{kind}"
        stored = get_storage(settings).put(key, bytes(content))
        if stored.sha256 != digest or stored.byte_size != len(content):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "source_storage_verification_failed",
                    "message": "Original document bytes could not be verified after storage.",
                },
            )
        source.storage_key = stored.key
        document = Document(
            source_id=source.id,
            source_version=source.version,
            extractor_version=EXTRACTOR_VERSION,
            chunker_version=CHUNKER_VERSION,
            state="queued",
            stage="queued",
            progress=0,
            details={"warnings": [], "languages": []},
        )
        session.add(document)
        session.flush()
        dedupe_key = (
            f"ingest_document:{document.id}:{digest[:16]}:"
            f"{EXTRACTOR_VERSION}:{CHUNKER_VERSION}"
        )
        session.add(
            Job(
                kind="ingest_document",
                run_id=None,
                dedupe_key=dedupe_key,
                payload={"document_id": document.id},
                state="queued",
                max_attempts=settings.job_max_attempts,
            )
        )
        session.commit()
        session.refresh(source)
        session.refresh(document)
        return {
            "source": _source_view(source),
            "document": _document_view(document, source),
        }
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        await file.close()


@router.get("/api/workspaces/{workspace_id}/documents")
def documents(workspace_id: UUID, session: Db) -> list[dict[str, Any]]:
    if session.get(Workspace, str(workspace_id)) is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return [
        _document_view(document, source)
        for document, source in list_documents(session, str(workspace_id))
    ]


@router.get("/api/documents/{document_id}/blocks")
def document_blocks(
    document_id: UUID,
    session: Db,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict[str, Any]:
    try:
        document, rows, total = get_document_blocks(
            session, str(document_id), offset=offset, limit=limit
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    source = session.get(Source, document.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Document source not found")
    selected: list[dict[str, Any]] = []
    used = 0
    for row in rows:
        value = {
            "id": row.id,
            "ordinal": row.ordinal,
            "kind": row.kind,
            "text": row.text,
            "heading": row.heading,
            "location": row.location,
            "language": row.language,
            "scripts": row.scripts,
        }
        size = len(row.text.encode("utf-8")) + 512
        if used + size > MAX_BLOCK_RESPONSE_BYTES:
            break
        selected.append(value)
        used += size
    next_offset = offset + len(selected)
    return {
        "document_id": document.id,
        "source_id": source.id,
        "offset": offset,
        "limit": limit,
        "total": total,
        "blocks": selected,
        "next_offset": next_offset if next_offset < total else None,
        "truncated": len(selected) < len(rows) or next_offset < total,
    }
