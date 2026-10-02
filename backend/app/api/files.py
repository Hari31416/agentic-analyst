"""HTTP routes for immutable CSV and spreadsheet sources."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import Field, StrictStr, model_validator
from sqlalchemy.orm import Session

from app.config import get_settings
from app.contracts import Contract
from app.db.models import Dataset, Source, Workspace
from app.db.session import get_session
from app.sources.files import (
    FileIngestionError,
    get_dataset_profile,
    get_dataset_rows,
    get_source_datasets,
    ingest_file,
)
from app.storage.factory import get_storage

router = APIRouter(tags=["sources"])
Db = Annotated[Session, Depends(get_session)]


class PatchSourceMetadata(Contract):
    description: StrictStr | None = Field(default=None, max_length=2_000)
    metric_hints: dict[StrictStr, StrictStr] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_patch(self) -> PatchSourceMetadata:
        if not self.model_fields_set:
            raise ValueError("at least one source metadata field is required")
        if self.metric_hints is not None:
            normalized: dict[str, str] = {}
            for raw_key, raw_value in self.metric_hints.items():
                key = raw_key.strip()
                value = raw_value.strip()
                if not key or len(key) > 128:
                    raise ValueError("metric hint keys must be 1 to 128 characters")
                if not value or len(value) > 500:
                    raise ValueError("metric hints must be 1 to 500 characters")
                normalized[key] = value
            if len(normalized) != len(self.metric_hints):
                raise ValueError("metric hint keys must be unique after trimming")
            self.metric_hints = normalized
        if self.description is not None:
            description = self.description.strip()
            self.description = description or None
        return self


def _source_view(
    source: Source, datasets: list[Dataset] | None = None
) -> dict[str, Any]:
    view: dict[str, Any] = {
        "id": source.id,
        "display_name": source.display_name,
        "kind": source.kind,
        "state": source.state,
        "version": source.version,
        "content_hash": source.content_hash,
        "description": source.details.get("description"),
        "metric_hints": source.details.get("metric_hints", {}),
    }
    if "error" in source.details:
        view["error"] = source.details["error"]
    if datasets is not None:
        view["datasets"] = [_dataset_view(dataset) for dataset in datasets]
    return view


def _dataset_view(dataset: Dataset) -> dict[str, Any]:
    details = dataset.details.get("details", dataset.details)
    return {
        "id": dataset.id,
        "source_id": dataset.source_id,
        "source_version": dataset.source_version,
        "identity": dataset.identity,
        "schema_version": dataset.schema_version,
        "designation": dataset.designation,
        "details": details,
    }


def _http_error(error: FileIngestionError) -> HTTPException:
    if error.code == "workspace_not_found":
        status = 404
    elif error.code in {
        "file_type_unsupported",
        "file_type_mismatch",
        "macros_rejected",
    }:
        status = 415
    elif error.code == "upload_too_large":
        status = 413
    else:
        status = 400
    return HTTPException(
        status_code=status, detail={"code": error.code, "message": str(error)}
    )


@router.post("/api/workspaces/{workspace_id}/sources/files", status_code=201)
async def upload_file(
    workspace_id: UUID, file: Annotated[UploadFile, File()], session: Db
) -> dict[str, Any]:
    settings = get_settings()
    workspace = session.get(Workspace, str(workspace_id))
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    filename = file.filename or ""
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
                    detail="Uploaded file exceeds the configured size limit",
                )
        try:
            source, datasets = ingest_file(
                session,
                get_storage(settings),
                str(workspace_id),
                filename,
                bytes(content),
                max_bytes=settings.max_upload_bytes,
            )
        except FileIngestionError as error:
            raise _http_error(error) from error
        return _source_view(source, datasets)
    finally:
        await file.close()


@router.patch("/api/workspaces/{workspace_id}/sources/{source_id}")
def patch_source_metadata(
    workspace_id: UUID,
    source_id: UUID,
    body: PatchSourceMetadata,
    session: Db,
) -> dict[str, Any]:
    source = session.get(Source, str(source_id))
    if source is None or source.workspace_id != str(workspace_id):
        raise HTTPException(status_code=404, detail="Source not found")
    details = dict(source.details or {})
    if "description" in body.model_fields_set:
        details["description"] = body.description
    if "metric_hints" in body.model_fields_set:
        details["metric_hints"] = body.metric_hints or {}
    source.details = details
    session.commit()
    session.refresh(source)
    return _source_view(source)


@router.get("/api/sources/{source_id}/datasets")
def source_datasets(source_id: UUID, session: Db) -> list[dict[str, Any]]:
    source, datasets = get_source_datasets(session, str(source_id))
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if source.state != "ready":
        raise HTTPException(status_code=409, detail="Source is not ready")
    return [_dataset_view(dataset) for dataset in datasets]


@router.get("/api/datasets/{dataset_id}/profile")
def dataset_profile(dataset_id: UUID, session: Db) -> dict[str, Any]:
    dataset, source = get_dataset_profile(session, str(dataset_id))
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    if source is None:
        raise HTTPException(status_code=409, detail="Dataset source is not ready")
    return _dataset_view(dataset)


@router.get("/api/datasets/{dataset_id}/rows")
def dataset_rows(
    dataset_id: UUID,
    session: Db,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict[str, Any]:
    settings = get_settings()
    dataset = session.get(Dataset, str(dataset_id))
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    source = session.get(Source, dataset.source_id)
    if source is None or source.state != "ready":
        raise HTTPException(status_code=409, detail="Dataset source is not ready")
    if source.kind in {"mysql", "postgresql"}:
        from app.sources.connections import ConnectorError, sample_dataset_rows

        try:
            database_result = sample_dataset_rows(
                session, dataset, settings, offset=offset, limit=limit
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except ConnectorError as error:
            status = (
                400 if error.code in {"invalid_offset", "invalid_row_limit"} else 503
            )
            raise HTTPException(
                status_code=status,
                detail={"code": error.code, "message": error.message},
            ) from error
        database_result.setdefault("dataset_id", dataset.id)
        return database_result
    try:
        file_result = get_dataset_rows(
            session,
            get_storage(settings),
            str(dataset_id),
            offset=offset,
            limit=limit,
            max_bytes=settings.max_upload_bytes,
            max_response_bytes=min(
                max(settings.max_result_bytes, 256 * 1024), 1024 * 1024
            ),
        )
    except FileIngestionError as error:
        if error.code == "source_unavailable":
            raise HTTPException(status_code=409, detail=str(error)) from error
        raise _http_error(error) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if file_result is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return file_result
