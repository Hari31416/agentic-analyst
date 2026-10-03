"""Register durable CSV output artifacts as reusable workspace datasets."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.artifacts import _read, artifact_kind
from app.db.models import Artifact, Dataset, Run, Source, Thread
from app.db.session import get_session
from app.sources.files import FileIngestionError, profile_upload

router = APIRouter(tags=["artifacts"])
Db = Annotated[Session, Depends(get_session)]


class RegisterDatasetRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)


def _validate_source(
    session: Session,
    source_id: str,
    workspace_id: str,
    expected_version: int | None,
) -> Source:
    source = session.get(Source, source_id)
    if source is None or source.workspace_id != workspace_id:
        raise HTTPException(409, "Artifact source lineage is unavailable")
    if (
        source.state != "ready"
        or expected_version is None
        or source.version != expected_version
    ):
        raise HTTPException(409, "Artifact source lineage is stale")
    return source


def _validate_lineage(
    session: Session, artifact: Artifact, run: Run, workspace_id: str
) -> list[str]:
    versions = run.config.get("source_versions", {})
    if not isinstance(versions, dict):
        raise HTTPException(409, "Artifact source lineage is stale")

    source_ids = set(run.selected_source_ids or [])
    checked_sources: set[str] = set()
    for source_id in source_ids:
        version = versions.get(source_id)
        _validate_source(
            session,
            source_id,
            workspace_id,
            version if isinstance(version, int) else None,
        )
        checked_sources.add(source_id)

    lineage_dataset_ids: set[str] = set()
    artifact_refs: list[tuple[str, str | None]] = []
    for item in artifact.lineage or []:
        if not isinstance(item, str):
            continue
        if item.startswith("source:"):
            reference = item.removeprefix("source:")
            source_id, separator, raw_version = reference.rpartition("@")
            expected = versions.get(source_id)
            if separator:
                version_digits = raw_version.removeprefix("v")
                if not version_digits or any(
                    character not in "0123456789" for character in version_digits
                ):
                    raise HTTPException(409, "Artifact source lineage is stale")
                lineage_version = int(version_digits)
                if lineage_version < 1:
                    raise HTTPException(409, "Artifact source lineage is stale")
                if expected != lineage_version:
                    raise HTTPException(409, "Artifact source lineage is stale")
            _validate_source(
                session,
                source_id,
                workspace_id,
                expected if isinstance(expected, int) else None,
            )
            checked_sources.add(source_id)
        elif item.startswith("dataset:"):
            lineage_dataset_ids.add(item.removeprefix("dataset:").split("@", 1)[0])
        elif item.startswith("artifact:"):
            reference = item.removeprefix("artifact:")
            artifact_id, separator, suffix = reference.partition("@sha256:")
            artifact_refs.append((artifact_id, suffix if separator else None))
        elif item in source_ids:
            version = versions.get(item)
            _validate_source(
                session,
                item,
                workspace_id,
                version if isinstance(version, int) else None,
            )
            checked_sources.add(item)

    for dataset_id in lineage_dataset_ids:
        dataset = session.get(Dataset, dataset_id)
        if dataset is None:
            raise HTTPException(409, "Artifact dataset lineage is unavailable")
        source = session.get(Source, dataset.source_id)
        if source is None or source.workspace_id != workspace_id:
            raise HTTPException(409, "Artifact dataset lineage is unavailable")
        if (
            source.state != "ready"
            or dataset.source_version != source.version
            or (
                dataset.source_id in versions
                and versions[dataset.source_id] != dataset.source_version
            )
        ):
            raise HTTPException(409, "Artifact dataset lineage is stale")

    for reference_id, expected_hash in artifact_refs:
        referenced = session.get(Artifact, reference_id)
        if (
            referenced is None
            or not referenced.durable
            or (expected_hash is not None and referenced.sha256 != expected_hash)
        ):
            raise HTTPException(409, "Artifact lineage is unavailable or stale")
        producer_run = (
            session.get(Run, referenced.run_id) if referenced.run_id else None
        )
        producer_thread = (
            session.get(Thread, producer_run.thread_id) if producer_run else None
        )
        if producer_thread is None or producer_thread.workspace_id != workspace_id:
            raise HTTPException(409, "Artifact lineage is unavailable")

    return list(artifact.lineage or [])


@router.post("/api/artifacts/{artifact_id}/dataset", status_code=201)
def register_artifact_dataset(
    artifact_id: UUID,
    session: Db,
    body: RegisterDatasetRequest | None = None,
) -> dict[str, Any]:
    artifact = session.scalar(
        select(Artifact)
        .where(Artifact.id == str(artifact_id), Artifact.durable.is_(True))
        .with_for_update()
    )
    if artifact is None:
        raise HTTPException(404, "Artifact not found")
    if artifact.media_type != "text/csv" or artifact_kind(artifact) != "table":
        raise HTTPException(415, "Only CSV table artifacts can be reused as datasets")
    run = session.get(Run, artifact.run_id) if artifact.run_id else None
    thread = session.get(Thread, run.thread_id) if run else None
    if run is None or thread is None:
        raise HTTPException(404, "Artifact workspace not found")
    lineage = _validate_lineage(session, artifact, run, thread.workspace_id)

    existing = next(
        (
            source
            for source in session.scalars(
                select(Source).where(Source.workspace_id == thread.workspace_id)
            )
            if (source.details or {}).get("artifact_id") == artifact.id
        ),
        None,
    )
    if existing is not None:
        if existing.state != "ready":
            raise HTTPException(409, "Registered dataset is no longer available")
        existing_dataset_ids = list(
            session.scalars(select(Dataset.id).where(Dataset.source_id == existing.id))
        )
        if not existing_dataset_ids:
            raise HTTPException(409, "Registered dataset metadata is unavailable")
        response = {
            "source_id": existing.id,
            "dataset_ids": existing_dataset_ids,
            "display_name": existing.display_name,
            "reused": True,
        }
        session.commit()
        return response

    content = _read(artifact)
    if hashlib.sha256(content).hexdigest() != artifact.sha256:
        raise HTTPException(409, "Artifact integrity check failed")
    try:
        profiles = profile_upload("result.csv", content)
    except FileIngestionError as error:
        raise HTTPException(415, str(error)) from error
    if not profiles:
        raise HTTPException(415, "CSV artifact contains no reusable dataset")

    display_name = (body.display_name if body else None) or artifact.display_name
    source = Source(
        workspace_id=thread.workspace_id,
        kind="csv",
        display_name=display_name,
        state="ready",
        storage_key=artifact.storage_key,
        content_hash=artifact.sha256,
        schema_version="derived-csv-v1",
        details={
            "designation": "derived",
            "artifact_id": artifact.id,
            "producer_run_id": artifact.run_id,
            "lineage": lineage,
        },
    )
    session.add(source)
    session.flush()
    dataset_ids: list[str] = []
    for profile in profiles:
        dataset = Dataset(
            source_id=source.id,
            source_version=source.version,
            identity=str(profile.get("sheet_name") or "result"),
            schema_version=hashlib.sha256(
                json.dumps(profile, sort_keys=True).encode()
            ).hexdigest(),
            details=profile,
            storage_key=artifact.storage_key,
            designation="derived",
            lineage=[*lineage, f"artifact:{artifact.id}"],
        )
        session.add(dataset)
        session.flush()
        dataset_ids.append(dataset.id)

    response = {
        "source_id": source.id,
        "dataset_ids": dataset_ids,
        "display_name": source.display_name,
        "reused": False,
    }
    session.commit()
    return response
