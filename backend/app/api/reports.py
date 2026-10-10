"""Personal report CRUD with frozen inputs and immutable generated versions."""

from __future__ import annotations

import hashlib
from contextlib import suppress
from datetime import timezone
from typing import Annotated, Any, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.resource_lifecycle import lock_workspace, queue_blob_deletion
from app.artifacts.tabular import decode_table, _string
from app.audit.redaction import contains_secret, redact
from app.auth.security import require_auth
from app.config import get_settings
from app.db.models import (
    Artifact,
    Evidence,
    Job,
    Message,
    Pin,
    Report,
    ReportAsset,
    ReportVersion,
    Run,
    Source,
    Thread,
    User,
    now,
)
from app.db.session import get_session
from app.storage.factory import get_storage
from app.storage.s3 import StorageUnavailable

router = APIRouter(prefix="/api", tags=["reports"])
Db = Annotated[Session, Depends(get_session)]
Owner = Annotated[User, Depends(require_auth)]
MAX_ASSET_BYTES = 20 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 50 * 1024 * 1024
MAX_MESSAGES = 200
MAX_TEXT = 100_000
REPORT_ERRORS = {
    "report_glyph_unsupported": "Some report text uses characters outside the bundled English and Devanagari fonts. Revise the text or choose different source material.",
    "report_render_unsupported": "An embedded chart or table uses an unsupported layout. Review the selected artifacts and regenerate with a simpler layout.",
    "report_context_too_large": "The selected material exceeds the model context limit. Create a report with fewer pins.",
    "report_reference_missing": "The model omitted a selected artifact. Retry with instructions to include all selected artifacts.",
    "report_structure_changed": "The revision changed protected artifact references or structure. Retry with narrower feedback.",
    "report_asset_integrity_failed": "A retained report asset failed its integrity check.",
    "report_asset_unavailable": "A retained report asset could not be read.",
    "model_not_configured": "Configure the model provider before generating reports.",
    "model_timeout": "The model provider timed out. Try creating a new report or regenerating a ready version.",
    "retry_limit_exceeded": "Worker attempts were exhausted. Create a new report or regenerate a ready version.",
}


class ReportTitle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value or contains_secret(value):
            raise ValueError("Title is blank or contains credentials")
        return value


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=200)
    language: Literal["en-IN", "hi-IN"] = "en-IN"
    pin_ids: list[UUID] = Field(min_length=1, max_length=30)
    instructions: str = Field(default="", max_length=4000)

    @field_validator("title")
    @classmethod
    def clean_optional_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if contains_secret(value):
            raise ValueError("Title contains credentials")
        return value or None


class Regenerate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: UUID
    feedback: str = Field(default="", max_length=4000)
    mode: Literal["wording", "restructure"] = "wording"


def timestamp(value: Any) -> Any:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def version_view(version: ReportVersion, *, document: bool = True) -> dict[str, Any]:
    return {
        "id": version.id,
        "number": version.number,
        "state": version.state,
        "language": version.language,
        "created_at": timestamp(version.created_at),
        "error": (
            REPORT_ERRORS.get(
                version.error,
                "Report generation failed. Create a new report or regenerate a ready version.",
            )
            if version.error
            else None
        ),
        "error_code": version.error,
        "document": redact(version.document) if document else None,
        "feedback": redact(version.feedback),
        "mode": version.mode,
        "base_version_id": version.base_version_id,
        "assets": {
            identity: {
                key: metadata.get(key)
                for key in ("display_name", "media_type", "byte_size", "sha256")
            }
            for identity, metadata in version.snapshot.get("assets", {}).items()
        },
    }


def report_view(
    session: Session, report: Report, *, detail: bool = False
) -> dict[str, Any]:
    versions = list(
        session.scalars(
            select(ReportVersion)
            .where(ReportVersion.report_id == report.id)
            .order_by(ReportVersion.number.desc())
        )
    )
    result = {
        "id": report.id,
        "workspace_id": report.workspace_id,
        "title": redact(report.title),
        "created_at": timestamp(report.created_at),
        "updated_at": timestamp(report.updated_at),
        "latest_version": (
            version_view(versions[0], document=detail) if versions else None
        ),
    }
    if detail:
        result["versions"] = [version_view(version) for version in versions]
    return result


def owned_report(
    session: Session, report_id: str, user: User, *, lock: bool = False
) -> Report:
    query = select(Report).where(Report.id == report_id, Report.user_id == user.id)
    if lock:
        # Common lock order with workspace deletion and other report mutations.
        workspace_id = session.scalar(
            select(Report.workspace_id).where(
                Report.id == report_id, Report.user_id == user.id
            )
        )
        if workspace_id is None:
            raise HTTPException(404, "Report not found")
        lock_workspace(session, workspace_id)
        query = query.with_for_update().execution_options(populate_existing=True)
    report = session.scalar(query)
    if report is None:
        raise HTTPException(404, "Report not found")
    return report


def get_version(session: Session, report: Report, version_id: str) -> ReportVersion:
    version = session.get(ReportVersion, version_id)
    if version is None or version.report_id != report.id:
        raise HTTPException(404, "Report version not found")
    return version


def build_snapshot(
    session: Session, report: Report, body: ReportCreate, user: User, copied: list[str]
) -> dict[str, Any]:
    """Expand ordered selections, deduplicate turns, and retain verified assets."""
    messages: dict[str, Message] = {}
    artifacts: dict[str, Artifact] = {}
    selection: list[dict[str, Any]] = []
    for pin_id in dict.fromkeys(str(identity) for identity in body.pin_ids):
        row = session.execute(
            select(Pin, Thread)
            .join(Thread, Pin.thread_id == Thread.id)
            .where(
                Pin.id == pin_id,
                Pin.user_id == user.id,
                Thread.workspace_id == report.workspace_id,
            )
        ).first()
        if row is None:
            raise HTTPException(404, "Selected pin not found in this workspace")
        pin, thread = row
        selection.append(
            {
                "pin_id": pin.id,
                "kind": pin.kind,
                "target_id": pin.target_id,
                "title": redact(pin.title),
                "notes": redact(pin.notes),
                "thread_id": thread.id,
                "thread_label": redact(thread.label),
            }
        )
        if pin.kind == "artifact":
            artifact = session.get(Artifact, pin.artifact_id)
            if artifact is None or not artifact.durable:
                raise HTTPException(422, "Selected artifact is unavailable")
            artifacts[artifact.id] = artifact
            continue
        query = select(Message).where(Message.thread_id == thread.id)
        if pin.kind == "message":
            message = session.get(Message, pin.message_id)
            if message is None:
                raise HTTPException(404, "Selected message is unavailable")
            query = (
                query.where(Message.run_id == message.run_id)
                if message.run_id
                else query.where(Message.id == message.id)
            )
        selected = list(
            session.scalars(
                query.order_by(Message.created_at, Message.id).limit(MAX_MESSAGES + 1)
            )
        )
        for message in selected:
            messages.setdefault(message.id, message)
        if len(messages) > MAX_MESSAGES:
            raise HTTPException(
                422, "Select fewer messages; reports support at most 200 messages"
            )
    if sum(len(message.content) for message in messages.values()) > MAX_TEXT:
        raise HTTPException(
            422, "Selected messages exceed the report text limit; select fewer pins"
        )
    run_ids = {message.run_id for message in messages.values() if message.run_id}
    provenance_run_ids = run_ids | {
        artifact.run_id for artifact in artifacts.values() if artifact.run_id
    }
    runs = list(session.scalars(select(Run).where(Run.id.in_(provenance_run_ids))))
    if any(run.state in {"queued", "running"} for run in runs):
        raise HTTPException(
            409, "Wait for selected chat turns to finish before generating a report"
        )
    # Include generated outputs for the selected turns, without execution code or inputs.
    for artifact in session.scalars(
        select(Artifact)
        .where(
            Artifact.run_id.in_(run_ids),
            Artifact.durable.is_(True),
            Artifact.role == "output",
        )
        .order_by(Artifact.created_at, Artifact.id)
        .limit(31)
    ):
        artifacts.setdefault(artifact.id, artifact)
    if (
        len(artifacts) > 30
        or sum(a.byte_size for a in artifacts.values()) > MAX_SNAPSHOT_BYTES
    ):
        raise HTTPException(
            422, "Select fewer artifacts; report assets exceed the retention limit"
        )
    if not messages and not artifacts:
        raise HTTPException(
            422, "Selected pins contain no messages or retained artifacts"
        )
    storage = get_storage(get_settings())
    assets: dict[str, dict[str, Any]] = {}
    for artifact in artifacts.values():
        if artifact.byte_size < 0 or artifact.byte_size > MAX_ASSET_BYTES:
            raise HTTPException(
                422, "A selected artifact exceeds the 20 MB report limit"
            )
        try:
            content = storage.read(artifact.storage_key, MAX_ASSET_BYTES)
        except (OSError, ValueError, StorageUnavailable) as error:
            raise HTTPException(422, "A selected artifact could not be read") from error
        if (
            len(content) != artifact.byte_size
            or hashlib.sha256(content).hexdigest() != artifact.sha256
        ):
            raise HTTPException(
                422, "A selected artifact failed integrity verification"
            )
        # Report-specific immutable copies have no FK to the original chat.
        key = f"derived/{report.workspace_id}/reports/{report.id}/assets/{artifact.id}"
        stored = storage.put(key, content)
        copied.append(key)
        asset = ReportAsset(
            report_id=report.id,
            original_artifact_id=artifact.id,
            storage_key=stored.key,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            media_type=artifact.media_type,
            display_name=redact(artifact.display_name),
        )
        session.add(asset)
        assets[artifact.id] = {
            "storage_key": stored.key,
            "sha256": stored.sha256,
            "byte_size": stored.byte_size,
            "media_type": artifact.media_type,
            "display_name": redact(artifact.display_name),
            "lineage": redact(artifact.lineage),
        }
    # Search tools retain every retrieved passage and repeat their retrieval trace
    # on each row. Report inputs need the answer's citations, not that search log.
    cited_ids = {
        str(identity)
        for message in messages.values()
        for identity in message.references.get("evidence_ids", [])
    }
    legacy_run_ids = {
        message.run_id
        for message in messages.values()
        if message.role == "assistant"
        and "evidence_ids" not in message.references
        and message.run_id
    }
    evidence = list(
        session.scalars(
            select(Evidence)
            .where(
                Evidence.run_id.in_(run_ids),
                (Evidence.id.in_(cited_ids) | Evidence.run_id.in_(legacy_run_ids)),
            )
            .order_by(Evidence.created_at, Evidence.id)
            .limit(201)
        )
    )
    evidence_records = [
        {
            "id": e.id,
            "kind": e.kind,
            "source_ids": e.source_ids,
            "details": redact(
                {key: value for key, value in e.details.items() if key != "trace"}
            ),
        }
        for e in evidence
    ]
    evidence_chars = sum(len(str(item["details"])) for item in evidence_records)
    if len(evidence) > 200 or evidence_chars > MAX_TEXT:
        raise HTTPException(
            422,
            f"Selected answers cite {len(evidence)} evidence records containing "
            f"{evidence_chars:,} characters. Reports support up to 200 records and "
            "100,000 evidence characters; choose answers with fewer citations.",
        )
    source_ids = {
        identity
        for message in messages.values()
        for identity in message.selected_source_ids
    }
    source_ids.update(identity for item in evidence for identity in item.source_ids)
    sources = list(
        session.scalars(
            select(Source).where(
                Source.id.in_(source_ids), Source.workspace_id == report.workspace_id
            )
        )
    )
    if not body.title:
        first_title = selection[0]["title"] or selection[0]["thread_label"]
        prefix = "रिपोर्ट: " if body.language == "hi-IN" else "Report: "
        report.title = prefix + first_title[: 200 - len(prefix)]
    return {
        "title": report.title,
        "instructions": body.instructions,
        "selection": selection,
        "assets": assets,
        "runs": [
            {
                "id": run.id,
                "source_versions": run.config.get("source_versions", {}),
                "source_schema_versions": run.config.get("source_schema_versions", {}),
            }
            for run in runs
        ],
        "messages": [
            {
                "id": m.id,
                "thread_id": m.thread_id,
                "run_id": m.run_id,
                "role": m.role,
                "content": redact(m.content),
                "references": redact(m.references),
            }
            for m in messages.values()
        ],
        "evidence": evidence_records,
        "sources": [
            {
                "id": s.id,
                "current_version": s.version,
                "current_content_hash": s.content_hash,
                "current_schema_version": s.schema_version,
                "display_name": redact(s.display_name),
            }
            for s in sources
        ],
    }


def enqueue(session: Session, report: Report, version: ReportVersion) -> None:
    session.add(version)
    session.flush()
    session.add(
        Job(
            kind="report_generation",
            workspace_id=report.workspace_id,
            dedupe_key=f"report_generation:{version.id}",
            payload={"version_id": version.id},
            max_attempts=2,
        )
    )


@router.get("/report-languages")
def languages() -> list[dict[str, str]]:
    from app.reports.renderer import capabilities

    return capabilities()


@router.get("/workspaces/{workspace_id}/reports")
def list_reports(
    workspace_id: UUID,
    session: Db,
    user: Owner,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> list[dict[str, Any]]:
    lock_workspace(session, str(workspace_id))
    reports = session.scalars(
        select(Report)
        .where(Report.workspace_id == str(workspace_id), Report.user_id == user.id)
        .order_by(Report.updated_at.desc(), Report.id)
        .offset(offset)
        .limit(limit)
    )
    return [report_view(session, report) for report in reports]


@router.post("/workspaces/{workspace_id}/reports", status_code=201)
def create_report(
    workspace_id: UUID, body: ReportCreate, session: Db, user: Owner
) -> dict[str, Any]:
    if contains_secret(body.instructions):
        raise HTTPException(422, "Instructions contain configured credentials")
    lock_workspace(session, str(workspace_id))
    report = Report(
        id=str(uuid4()),
        workspace_id=str(workspace_id),
        user_id=user.id,
        title=body.title or "Report",
    )
    session.add(report)
    session.flush()
    copied: list[str] = []
    try:
        snapshot = build_snapshot(session, report, body, user, copied)
        version = ReportVersion(
            report_id=report.id,
            number=1,
            language=body.language,
            snapshot=snapshot,
            mode="initial",
        )
        enqueue(session, report, version)
        session.commit()
    except Exception:
        session.rollback()
        storage = get_storage(get_settings())
        for key in copied:
            with suppress(Exception):
                storage.delete(key)
        raise
    return report_view(session, report, detail=True)


@router.get("/reports/{report_id}")
def read_report(report_id: UUID, session: Db, user: Owner) -> dict[str, Any]:
    return report_view(
        session, owned_report(session, str(report_id), user), detail=True
    )


@router.patch("/reports/{report_id}")
def rename_report(
    report_id: UUID, body: ReportTitle, session: Db, user: Owner
) -> dict[str, Any]:
    report = owned_report(session, str(report_id), user, lock=True)
    report.title, report.updated_at = body.title, now()
    session.commit()
    return report_view(session, report, detail=True)


@router.post("/reports/{report_id}/regenerate", status_code=201)
def regenerate_report(
    report_id: UUID, body: Regenerate, session: Db, user: Owner
) -> dict[str, Any]:
    if contains_secret(body.feedback):
        raise HTTPException(422, "Feedback contains configured credentials")
    report = owned_report(session, str(report_id), user, lock=True)
    base = get_version(session, report, str(body.version_id))
    if base.state != "ready" or not base.document:
        raise HTTPException(409, "Regeneration requires a ready version")
    if session.scalar(
        select(ReportVersion.id).where(
            ReportVersion.report_id == report.id,
            ReportVersion.state.in_(["queued", "generating"]),
        )
    ):
        raise HTTPException(409, "Wait for the current report version to finish")
    number = (
        int(
            session.scalar(
                select(func.max(ReportVersion.number)).where(
                    ReportVersion.report_id == report.id
                )
            )
            or 0
        )
        + 1
    )
    version = ReportVersion(
        report_id=report.id,
        number=number,
        language=base.language,
        snapshot=base.snapshot,
        feedback=body.feedback,
        mode=body.mode,
        base_version_id=base.id,
    )
    enqueue(session, report, version)
    report.updated_at = now()
    session.commit()
    return report_view(session, report, detail=True)


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(report_id: UUID, session: Db, user: Owner) -> Response:
    report = owned_report(session, str(report_id), user, lock=True)
    versions = list(
        session.scalars(
            select(ReportVersion).where(ReportVersion.report_id == report.id)
        )
    )
    ids = [v.id for v in versions]
    jobs = list(
        session.scalars(
            select(Job)
            .where(
                Job.kind == "report_generation",
                Job.payload["version_id"].as_string().in_(ids),
            )
            .with_for_update()
        )
    )
    keys = report_storage_keys(session, [report.id])
    # Cancelling the lease makes in-flight generation fail its final ownership guard.
    for job in jobs:
        job.state, job.lease_token, job.lease_expires_at = "cancelled", None, None
    queue_blob_deletion(session, keys)
    session.delete(report)
    session.commit()
    return Response(status_code=204)


def report_storage_keys(session: Session, report_ids: list[str]) -> set[str]:
    keys = set(
        session.scalars(
            select(ReportAsset.storage_key).where(ReportAsset.report_id.in_(report_ids))
        )
    )
    keys.update(
        key
        for key in session.scalars(
            select(ReportVersion.pdf_key).where(ReportVersion.report_id.in_(report_ids))
        )
        if key
    )
    return keys


def verified_response(
    key: str, sha256: str, byte_size: int, name: str, media_type: str
) -> Response:
    try:
        content = get_storage(get_settings()).read(key, byte_size)
    except (OSError, ValueError, StorageUnavailable) as error:
        raise HTTPException(409, "Retained report file is unavailable") from error
    if len(content) != byte_size or hashlib.sha256(content).hexdigest() != sha256:
        raise HTTPException(409, "Retained report file failed integrity verification")
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/reports/{report_id}/versions/{version_id}/download")
def download_report(
    report_id: UUID, version_id: UUID, session: Db, user: Owner
) -> Response:
    report = owned_report(session, str(report_id), user)
    version = get_version(session, report, str(version_id))
    if (
        version.state != "ready"
        or not version.pdf_key
        or not version.pdf_sha256
        or version.pdf_size is None
    ):
        raise HTTPException(409, "Report PDF is not ready")
    return verified_response(
        version.pdf_key,
        version.pdf_sha256,
        version.pdf_size,
        f"{report.title}-v{version.number}.pdf",
        "application/pdf",
    )


@router.get("/reports/{report_id}/versions/{version_id}/document")
def download_document(
    report_id: UUID, version_id: UUID, session: Db, user: Owner
) -> dict[str, Any]:
    report = owned_report(session, str(report_id), user)
    version = get_version(session, report, str(version_id))
    if version.state != "ready" or version.document is None:
        raise HTTPException(409, "Report document is not ready")
    return dict(redact(version.document))


@router.get("/reports/{report_id}/versions/{version_id}/assets/{artifact_id}")
def download_asset(
    report_id: UUID, version_id: UUID, artifact_id: UUID, session: Db, user: Owner
) -> Response:
    report = owned_report(session, str(report_id), user)
    version = get_version(session, report, str(version_id))
    asset = version.snapshot.get("assets", {}).get(str(artifact_id))
    if not isinstance(asset, dict):
        raise HTTPException(404, "Report asset not found")
    return verified_response(
        asset["storage_key"],
        asset["sha256"],
        asset["byte_size"],
        asset["display_name"],
        asset["media_type"],
    )


@router.get("/reports/{report_id}/versions/{version_id}/assets/{artifact_id}/table")
def preview_table(
    report_id: UUID,
    version_id: UUID,
    artifact_id: UUID,
    session: Db,
    user: Owner,
    max_rows: Annotated[int, Query(ge=1, le=100)] = 30,
    columns: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    report = owned_report(session, str(report_id), user)
    version = get_version(session, report, str(version_id))
    asset = version.snapshot.get("assets", {}).get(str(artifact_id))
    if not isinstance(asset, dict):
        raise HTTPException(404, "Report asset not found")
    response = verified_response(
        asset["storage_key"],
        asset["sha256"],
        asset["byte_size"],
        asset["display_name"],
        asset["media_type"],
    )
    try:
        headers, rows = decode_table(
            bytes(response.body), asset["display_name"], max_rows=max_rows + 1
        )
        if columns:
            if (
                len(columns) > 12
                or len(set(columns)) != len(columns)
                or any(column not in headers for column in columns)
            ):
                raise ValueError("Invalid table columns")
            indices = [headers.index(column) for column in columns]
            headers = columns
            rows = [[row[index] for index in indices] for row in rows]
        result = {
            "columns": headers,
            "rows": [[_string(cell) for cell in row] for row in rows[:max_rows]],
            "truncated": len(rows) > max_rows,
        }
        if contains_secret(str(result)):
            raise ValueError("Table contains configured credentials")
    except ValueError as error:
        raise HTTPException(
            422, "Retained table could not be previewed with these columns"
        ) from error
    return result
