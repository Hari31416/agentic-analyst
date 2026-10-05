"""Manifest, safe preview, chart, table pagination, and download routes."""

from __future__ import annotations

import json
import hashlib
from typing import Annotated, Literal, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.chart import ChartSpec
from app.artifacts.tabular import (
    _string,
    decode_table,
    safe_csv,
    safe_csv_export,
    safe_parquet,
    safe_xlsx,
)
from app.config import get_settings
from app.audit.redaction import contains_secret
from app.db.models import Artifact, Run, Thread, Workspace
from app.db.session import get_session
from app.storage.factory import get_storage
from app.storage.s3 import StorageUnavailable

router = APIRouter(tags=["artifacts"])
Db = Annotated[Session, Depends(get_session)]
DownloadFormat = Literal["original", "csv", "xlsx", "parquet"]
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PARQUET_TYPE = "application/vnd.apache.parquet"


def artifact_kind(row: Artifact) -> str:
    name = row.display_name.lower()
    media = row.media_type.lower().split(";", 1)[0].strip()
    if media.startswith("image/"):
        return "image"
    if media == "text/html" or name.endswith((".html", ".htm")):
        return "html"
    if name.endswith((".chart.json", "chart.json")):
        return "chart"
    if name.endswith(".pdf") or media == "application/pdf":
        return "report_pdf" if "report" in name else "pdf"
    if name.endswith(".md") or media == "text/markdown":
        return "report_markdown" if "report" in name else "markdown"
    if name.endswith(".ipynb"):
        return "report_notebook"
    if media in {"text/csv", XLSX_TYPE, PARQUET_TYPE} or name.endswith(
        (".csv", ".xlsx", ".xlsm", ".parquet")
    ):
        return "table"
    return "file"


def manifest(row: Artifact) -> dict[str, Any]:
    return {
        "id": row.id,
        "display_name": row.display_name,
        "media_type": row.media_type,
        "byte_size": row.byte_size,
        "sha256": row.sha256,
        "run_id": row.run_id,
        "lineage": row.lineage or [],
        "durable": row.durable,
        "artifact_type": artifact_kind(row),
        "role": row.role,
    }


def _get_artifact(session: Session, artifact_id: str) -> Artifact:
    artifact = session.get(Artifact, artifact_id)
    if artifact is None or not artifact.durable:
        raise HTTPException(404, "Artifact not found")
    return artifact


def _read(artifact: Artifact) -> bytes:
    settings = get_settings()
    try:
        storage = get_storage(settings)
        if not storage.verify(
            artifact.storage_key, artifact.sha256, artifact.byte_size
        ):
            raise HTTPException(409, "Artifact integrity check failed")
        content = storage.read(artifact.storage_key, settings.max_upload_bytes)
        if contains_secret(content):
            raise HTTPException(403, "Artifact contains configured credentials")
        return content
    except (OSError, ValueError, StorageUnavailable) as error:
        raise HTTPException(503, "Artifact bytes are unavailable") from error


@router.get("/api/workspaces/{workspace_id}/artifacts")
def workspace_artifacts(
    workspace_id: UUID, session: Db, thread_id: UUID | None = None
) -> list[dict[str, Any]]:
    workspace = session.get(Workspace, str(workspace_id))
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    if thread_id is not None:
        thread = session.get(Thread, str(thread_id))
        if thread is None or thread.workspace_id != str(workspace_id):
            raise HTTPException(404, "Chat not found in this workspace")
    statement = (
        select(Artifact)
        .join(Run, Artifact.run_id == Run.id)
        .join(Thread, Run.thread_id == Thread.id)
        .where(Thread.workspace_id == str(workspace_id), Artifact.durable.is_(True))
    )
    if thread_id is not None:
        statement = statement.where(Run.thread_id == str(thread_id))
    rows = session.scalars(statement.order_by(Artifact.created_at.desc()).limit(500))
    return [manifest(row) for row in rows]


@router.get("/api/artifacts/{artifact_id}")
def artifact_detail(
    artifact_id: UUID,
    session: Db,
    max_characters: Annotated[int, Query(ge=0, le=8000)] = 4000,
) -> dict[str, Any]:
    artifact = _get_artifact(session, str(artifact_id))
    view = manifest(artifact)
    if artifact.media_type.startswith("text/") or artifact.media_type in {
        "application/json",
        "application/vnd.plotly.v1+json",
        "application/x-ipynb+json",
    }:
        content = _read(artifact).decode("utf-8", errors="replace")
        view["text"] = content[:max_characters]
        view["truncated"] = len(content) > max_characters
    return view


@router.get("/api/artifacts/{artifact_id}/rows")
def artifact_rows(
    artifact_id: UUID,
    session: Db,
    offset: Annotated[int, Query(ge=0, le=2_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    artifact = _get_artifact(session, str(artifact_id))
    try:
        columns, rows = decode_table(
            _read(artifact), artifact.display_name, max_rows=250_001
        )
    except (ValueError, UnicodeError, json.JSONDecodeError) as error:
        raise HTTPException(
            415, "Artifact has no supported table representation"
        ) from error
    total = min(len(rows), 250_000)
    page = rows[offset : offset + limit]
    row_views: list[dict[str, str]] = []
    response_bytes = 0
    for row in page:
        view = {column: _string(value) for column, value in zip(columns, row)}
        row_bytes = sum(
            len(key.encode()) + len(value.encode()) for key, value in view.items()
        )
        if response_bytes + row_bytes > 1_000_000:
            break
        response_bytes += row_bytes
        row_views.append(view)
    return {
        "artifact_id": artifact.id,
        "columns": [{"name": column, "type": "string"} for column in columns],
        "rows": row_views,
        "offset": offset,
        "limit": limit,
        "total_rows": total,
        "truncated": len(rows) > 250_000 or len(row_views) < len(page),
    }


@router.get("/api/artifacts/{artifact_id}/chart")
def artifact_chart(artifact_id: UUID, session: Db) -> dict[str, Any]:
    artifact = _get_artifact(session, str(artifact_id))
    try:
        chart = ChartSpec.from_json_bytes(_read(artifact))
    except (ValueError, UnicodeError) as error:
        raise HTTPException(
            422, "Artifact does not satisfy the safe chart schema"
        ) from error
    return chart.plotly()


@router.get("/api/artifacts/{artifact_id}/download")
def artifact_download(
    artifact_id: UUID,
    session: Db,
    format: DownloadFormat = "original",
) -> Response:
    artifact = _get_artifact(session, str(artifact_id))
    content = _read(artifact)
    media_type = artifact.media_type
    filename = artifact.display_name
    headers: dict[str, str] = {"X-Content-Type-Options": "nosniff"}
    headers["X-Artifact-SHA256"] = artifact.sha256
    is_csv = media_type == "text/csv" or filename.lower().endswith(".csv")
    is_xlsx = filename.lower().endswith((".xlsx", ".xlsm"))
    if format != "original" or is_csv or is_xlsx:
        try:
            columns, rows = decode_table(
                content, artifact.display_name, max_rows=250_001
            )
            if len(rows) > 250_000:
                raise HTTPException(
                    413,
                    "Whole-file export exceeds 250,000 rows; use paginated table access or reduce the output",
                )
        except (ValueError, UnicodeError, json.JSONDecodeError) as error:
            if format != "original":
                raise HTTPException(
                    415, "Artifact cannot be exported as a table"
                ) from error
        else:
            if format == "original" and is_csv:
                content = safe_csv_export(content, columns, rows)
                filename = artifact.display_name
                media_type = "text/csv; charset=utf-8"
                headers["X-Export-Sanitized"] = "spreadsheet-formulas"
            elif format == "csv":
                content = safe_csv(columns, rows)
                filename = artifact.display_name.rsplit(".", 1)[0] + ".csv"
                media_type = "text/csv; charset=utf-8"
                headers["X-Export-Sanitized"] = "spreadsheet-formulas"
            elif format == "xlsx" or format == "original" and is_xlsx:
                content = safe_xlsx(columns, rows)
                filename = artifact.display_name.rsplit(".", 1)[0] + ".xlsx"
                media_type = XLSX_TYPE
                headers["X-Export-Sanitized"] = "spreadsheet-formulas"
            else:
                try:
                    content = safe_parquet(columns, rows)
                except ValueError as error:
                    raise HTTPException(503, str(error)) from error
                filename = artifact.display_name.rsplit(".", 1)[0] + ".parquet"
                media_type = PARQUET_TYPE
    headers["Content-Disposition"] = (
        f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
    )
    headers["X-Export-SHA256"] = hashlib.sha256(content).hexdigest()
    headers["Content-Security-Policy"] = "sandbox"
    return Response(content, media_type=media_type, headers=headers)
