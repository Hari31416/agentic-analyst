from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_session
from app.portability import PortabilityError, export_workspace, import_workspace
from app.portability.service import MAX_ARCHIVE_BYTES

router = APIRouter(tags=["portability"])
Db = Annotated[Session, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.get("/api/workspaces/{workspace_id}/export")
def export_portable_workspace(
    workspace_id: UUID, session: Db, settings: AppSettings
) -> Response:
    try:
        archive = export_workspace(session, str(workspace_id), settings)
    except PortabilityError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": error.message},
        ) from error
    return Response(
        content=archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="workspace-{workspace_id}.zip"',
            "Content-Length": str(len(archive)),
            "Cache-Control": "no-store",
        },
    )


@router.post("/api/portability/import", status_code=201)
async def import_portable_workspace(
    file: Annotated[UploadFile, File()], session: Db, settings: AppSettings
) -> dict[str, Any]:
    content = bytearray()
    try:
        while True:
            chunk = await file.read(
                min(64 * 1024, MAX_ARCHIVE_BYTES + 1 - len(content))
            )
            if not chunk:
                break
            content.extend(chunk)
            if len(content) > MAX_ARCHIVE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "archive_size_invalid",
                        "message": "Archive exceeds the upload limit.",
                    },
                )
        try:
            return import_workspace(session, bytes(content), settings)
        except PortabilityError as error:
            session.rollback()
            raise HTTPException(
                status_code=error.status_code,
                detail={"code": error.code, "message": error.message},
            ) from error
    finally:
        await file.close()
