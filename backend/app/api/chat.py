import asyncio
import hashlib
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit.redaction import contains_secret, redact
from app.agent.loop import PROMPT_VERSION
from app.config import get_settings
from app.contracts import Contract, RunEvent, RunState, TERMINAL_STATES
from app.db.models import (
    Artifact,
    Dataset,
    Job,
    Message,
    Run,
    Source,
    Thread,
    ToolCall,
    now,
)
from app.db.repository import append_event, audit, events_after
from app.db.session import factory, get_session
from app.language.metadata import LanguageMetadata
from app.language.text import analyze_text
from app.storage.factory import get_storage
from app.storage.s3 import StorageUnavailable

router = APIRouter(prefix="/api")
Db = Annotated[Session, Depends(get_session)]


class RunRequest(Contract):
    text: str = Field(min_length=1, max_length=20000)
    selected_source_ids: list[UUID] = Field(default_factory=list, max_length=100)
    selected_dataset_ids: list[UUID] = Field(default_factory=list, max_length=100)
    answer_language: str = "en-IN"
    retrieval_profile: Literal["basic", "advanced"] = "basic"
    request_id: UUID = Field(default_factory=uuid4)

    @field_validator("answer_language")
    @classmethod
    def language(cls, value: str) -> str:
        return LanguageMetadata(tags=[value]).tags[0]

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message cannot be blank")
        return value.strip()


def run_view(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "thread_id": run.thread_id,
        "state": run.state,
        "created_at": run.created_at,
        "outcome": redact(run.outcome),
        "selected_source_ids": run.selected_source_ids,
        "selected_dataset_ids": run.config.get("selected_dataset_ids", []),
        "answer_language": run.config.get("answer_language", "en-IN"),
        "retrieval_profile": run.config.get("retrieval_profile", "basic"),
    }


def get_run(session: Session, run_id: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/threads/{thread_id}/messages")
def messages(thread_id: str, session: Db) -> list[dict[str, Any]]:
    if session.get(Thread, thread_id) is None:
        raise HTTPException(404, "Thread not found")
    rows = session.scalars(
        select(Message)
        .where(Message.thread_id == thread_id)
        .order_by(Message.created_at, Message.id)
    )
    return [
        {
            "id": row.id,
            "role": row.role,
            "content": redact(row.content),
            "run_id": row.run_id,
            "references": redact(row.references),
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/threads/{thread_id}/runs")
def runs(thread_id: str, session: Db) -> list[dict[str, Any]]:
    if session.get(Thread, thread_id) is None:
        raise HTTPException(404, "Thread not found")
    return [
        run_view(row)
        for row in session.scalars(
            select(Run)
            .where(Run.thread_id == thread_id)
            .order_by(Run.created_at.desc())
            .limit(100)
        )
    ]


@router.post("/threads/{thread_id}/runs", status_code=201)
def create_run(thread_id: str, body: RunRequest, session: Db) -> dict[str, Any]:
    settings = get_settings()
    if contains_secret(body.text):
        raise HTTPException(422, "Message contains configured credentials")
    if not settings.model_configured:
        raise HTTPException(
            503,
            "Configure OPENAI_BASE_URL, OPENAI_API_KEY, and OPENAI_MODEL before starting chat",
        )
    if body.answer_language not in settings.supported_languages:
        raise HTTPException(422, "Requested answer language is unavailable")
    from app.api.resource_lifecycle import lock_thread

    thread = lock_thread(session, thread_id)
    selected = list(dict.fromkeys(str(i) for i in body.selected_source_ids))
    selected_datasets = list(dict.fromkeys(str(i) for i in body.selected_dataset_ids))
    if not selected:
        previous_run = session.scalar(
            select(Run)
            .where(Run.thread_id == thread_id)
            .order_by(Run.created_at.desc())
            .limit(1)
        )
        if previous_run and previous_run.selected_source_ids:
            selected = list(
                dict.fromkeys(str(i) for i in previous_run.selected_source_ids)
            )
            if not selected_datasets and previous_run.config.get(
                "selected_dataset_ids"
            ):
                selected_datasets = list(
                    dict.fromkeys(
                        str(i) for i in previous_run.config["selected_dataset_ids"]
                    )
                )
    language_metadata = analyze_text(
        body.text,
        body.answer_language,
        supported_languages=settings.supported_languages,
    )
    existing = session.get(Run, str(body.request_id))
    if existing:
        original = session.scalar(
            select(Message).where(Message.run_id == existing.id, Message.role == "user")
        )
        if (
            existing.thread_id != thread_id
            or original is None
            or original.content != body.text
            or existing.selected_source_ids != selected
            or existing.config.get("selected_dataset_ids", []) != selected_datasets
            or existing.config.get("answer_language") != body.answer_language
            or existing.config.get("retrieval_profile", "basic")
            != body.retrieval_profile
        ):
            raise HTTPException(409, "Request ID is already used by another input")
        return run_view(existing)
    active = session.scalar(
        select(Run.id)
        .where(Run.thread_id == thread_id, Run.state.in_(["queued", "running"]))
        .limit(1)
    )
    if active:
        raise HTTPException(409, "This thread already has an active run")
    source_rows = (
        list(
            session.scalars(
                select(Source).where(
                    Source.id.in_(selected),
                    Source.workspace_id == thread.workspace_id,
                    Source.state != "deleted",
                )
            )
        )
        if selected
        else []
    )
    if len(source_rows) != len(selected):
        raise HTTPException(422, "Selected sources must belong to this workspace")
    if selected_datasets:
        dataset_rows = list(
            session.scalars(
                select(Dataset).where(
                    Dataset.id.in_(selected_datasets), Dataset.source_id.in_(selected)
                )
            )
        )
        versions = {source.id: source.version for source in source_rows}
        if len(dataset_rows) != len(selected_datasets) or any(
            row.source_version != versions[row.source_id] for row in dataset_rows
        ):
            raise HTTPException(
                422,
                "Selected datasets must belong to the current selected source versions",
            )
    elif selected:
        dataset_rows = list(
            session.scalars(select(Dataset).where(Dataset.source_id.in_(selected)))
        )
        versions = {source.id: source.version for source in source_rows}
        selected_datasets = [
            row.id
            for row in dataset_rows
            if row.source_version == versions.get(row.source_id)
        ]
    run = Run(
        id=str(body.request_id),
        thread_id=thread_id,
        selected_source_ids=selected,
        config={
            "answer_language": body.answer_language,
            "language_metadata": language_metadata.model_dump(mode="json"),
            "retrieval_profile": body.retrieval_profile,
            "retrieval_pipeline_version": "advanced-retrieval-v1",
            "retrieval_settings": {
                "variant_limit": 3,
                "candidate_budget": 60,
                "rerank_default": False,
                "aliases": settings.retrieval_aliases,
                "embedding_model": settings.embedding_model,
                "embedding_revision": settings.embedding_revision,
                "reranker_model": settings.reranker_model,
                "reranker_revision": settings.reranker_revision,
                "summary_algorithm": "extractive-summary-v3",
                "token_budget_default": 12000,
            },
            "selected_dataset_ids": selected_datasets,
            "model": settings.openai_model,
            "prompt_version": PROMPT_VERSION,
            "policy_version": "execution-policy-v1",
            "source_versions": {source.id: source.version for source in source_rows},
            "max_tool_calls": settings.max_tool_calls,
            "max_model_calls": settings.max_model_calls,
        },
    )
    session.add(run)
    session.flush()
    session.add(
        Message(
            thread_id=thread_id,
            run_id=run.id,
            role="user",
            content=body.text,
            selected_source_ids=selected,
            references={"language_metadata": language_metadata.model_dump(mode="json")},
        )
    )
    session.add(
        Job(
            kind="agent_run",
            run_id=run.id,
            dedupe_key="run:" + run.id,
            payload={},
            max_attempts=settings.job_max_attempts,
        )
    )
    append_event(
        session,
        run.id,
        "status",
        {"state": "queued", "message": "Waiting for a worker"},
    )
    audit(
        session,
        run_id=run.id,
        action="run.create",
        decision="allowed",
        reason_code="selected_sources_valid",
        details={"source_ids": selected},
    )
    session.commit()
    return run_view(run)


@router.get("/runs/{run_id}")
def run_status(run_id: str, session: Db) -> dict[str, Any]:
    return run_view(get_run(session, run_id))


@router.get("/runs/{run_id}/retrieval")
def retrieval_traces(run_id: str, session: Db) -> dict[str, Any]:
    run = get_run(session, run_id)
    calls = session.scalars(
        select(ToolCall)
        .where(
            ToolCall.run_id == run_id,
            ToolCall.name.in_(
                ["search_documents", "source_passage", "summarize_documents"]
            ),
        )
        .order_by(ToolCall.created_at)
        .limit(100)
    )
    return {
        "profile": run.config.get("retrieval_profile", "basic"),
        "pipeline_version": run.config.get("retrieval_pipeline_version"),
        "settings": run.config.get("retrieval_settings", {}),
        "tools": [
            {
                "tool_call_id": call.id,
                "name": call.name,
                "status": call.status,
                "result_status": (call.result or {}).get("status"),
                "trace": (call.result or {}).get("data", {}).get("trace", []),
                "summary_cache": (call.result or {}).get("data", {}).get("cache"),
                "summary_method": (call.result or {}).get("data", {}).get("method"),
                "error": (call.result or {}).get("error"),
            }
            for call in calls
        ],
    }


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, session: Db) -> dict[str, Any]:
    # Match the worker lock order so cancellation and finalization are serialized.
    session.scalars(select(Job).where(Job.run_id == run_id).with_for_update()).all()
    run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise HTTPException(404, "Run not found")
    if run.state not in TERMINAL_STATES:
        run.state = RunState.CANCELLED
        run.finished_at = now()
        run.outcome = {
            "partial": True,
            "message": "Cancelled; retained outputs may be incomplete",
            "cleanup": "pending" if run.started_at else "complete",
        }
        # Keep a running worker's job leased until it confirms guest cleanup.
        # Queued work has no guest to stop.
        session.execute(
            update(Job)
            .where(Job.run_id == run_id, Job.state == "queued")
            .values(state="cancelled")
        )
        append_event(
            session,
            run.id,
            "status" if run.started_at else "terminal",
            {
                "state": "cancelled",
                "message": "Cancellation requested; active sandbox cleanup may still be pending",
            },
        )
        audit(
            session,
            run_id=run.id,
            action="run.cancel",
            decision="allowed",
            reason_code="user_requested",
        )
        session.commit()
    return run_view(run)


def event_batch(run_id: str, after: int) -> tuple[list[RunEvent], str, int]:
    with factory()() as session:
        run = get_run(session, run_id)
        return (
            [
                RunEvent.model_validate(row)
                for row in events_after(session, run_id, after)
            ],
            (
                "cleanup_pending"
                if (run.outcome or {}).get("cleanup") == "pending"
                else run.state
            ),
            run.event_sequence,
        )


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str, request: Request, last_event_id: Annotated[str | None, Header()] = None
) -> StreamingResponse:
    try:
        after = int(last_event_id or request.query_params.get("after", "0"))
        if after < 0:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(
            422, "Last event ID must be a nonnegative sequence"
        ) from exc
    await asyncio.to_thread(event_batch, run_id, after)

    async def stream() -> AsyncIterator[str]:
        sequence = after
        auth_checked = 0.0
        while not await request.is_disconnected():
            from app.auth.security import stream_authorized
            from datetime import datetime, timezone

            claims = getattr(request.state, "auth_claims", None)
            if claims is not None:
                if datetime.now(timezone.utc).timestamp() >= claims["exp"]:
                    return
                now = asyncio.get_running_loop().time()
                if now - auth_checked >= 5:
                    if not await asyncio.to_thread(stream_authorized, claims):
                        return
                    auth_checked = now
            batch, state, total = await asyncio.to_thread(event_batch, run_id, sequence)
            for event in batch:
                sequence = event.sequence
                yield f"id: {sequence}\ndata: {event.model_dump_json()}\n\n"
            if state in TERMINAL_STATES and sequence >= total:
                return
            if not batch:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}/artifacts")
def artifacts(run_id: str, session: Db) -> list[dict[str, Any]]:
    get_run(session, run_id)
    return [
        {
            "id": row.id,
            "display_name": row.display_name,
            "media_type": row.media_type,
            "byte_size": row.byte_size,
            "sha256": row.sha256,
            "role": row.role,
        }
        for row in session.scalars(
            select(Artifact)
            .where(Artifact.run_id == run_id)
            .order_by(Artifact.created_at)
        )
    ]


@router.get("/artifacts/{artifact_id}/content")
def artifact_content(artifact_id: str, session: Db) -> Response:
    from urllib.parse import quote
    from app.artifacts.tabular import decode_table, safe_csv_export, safe_xlsx

    artifact = session.get(Artifact, artifact_id)
    if artifact is None or not artifact.durable:
        raise HTTPException(404, "Artifact not found")
    storage = get_storage()
    try:
        if not storage.verify(
            artifact.storage_key, artifact.sha256, artifact.byte_size
        ):
            raise HTTPException(409, "Artifact integrity check failed")
        content = storage.read(artifact.storage_key, get_settings().max_upload_bytes)
        if contains_secret(content):
            raise HTTPException(403, "Artifact contains configured credentials")
    except (OSError, ValueError, StorageUnavailable) as exc:
        raise HTTPException(503, "Artifact bytes are unavailable") from exc
    disposition = "attachment"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "X-Artifact-SHA256": artifact.sha256,
    }
    media_type = artifact.media_type
    if artifact.media_type == "application/pdf":
        disposition = "inline"
    elif artifact.media_type == "text/csv" or artifact.display_name.lower().endswith(
        ".csv"
    ):
        try:
            columns, rows = decode_table(
                content, artifact.display_name, max_rows=250_001
            )
            if len(rows) > 250_000:
                raise HTTPException(
                    413, "Sanitized whole-file export exceeds 250,000 rows"
                )
            content = safe_csv_export(content, columns, rows)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(
                422, "CSV artifact could not be safely exported"
            ) from exc
        media_type = "text/csv; charset=utf-8"
        headers["X-Export-Sanitized"] = "spreadsheet-formulas"
    elif artifact.display_name.lower().endswith((".xlsx", ".xlsm")):
        try:
            columns, rows = decode_table(
                content, artifact.display_name, max_rows=250_001
            )
            if len(rows) > 250_000:
                raise HTTPException(
                    413, "Sanitized whole-file export exceeds 250,000 rows"
                )
            content = safe_xlsx(columns, rows)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(
                422, "Spreadsheet artifact could not be safely exported"
            ) from exc
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        headers["X-Export-Sanitized"] = "spreadsheet-formulas"
    headers["Content-Disposition"] = (
        f"{disposition}; filename*=UTF-8''{quote(artifact.display_name, safe='')}"
    )
    headers["X-Export-SHA256"] = hashlib.sha256(content).hexdigest()
    return Response(
        content,
        media_type=media_type,
        headers=headers,
    )


@router.get("/artifacts/{artifact_id}/preview")
def artifact_preview(
    artifact_id: str, session: Db, max_characters: int = 4000
) -> dict[str, Any]:
    if not 1 <= max_characters <= 8000:
        raise HTTPException(422, "Preview length must be from 1 to 8000 characters")
    artifact = session.get(Artifact, artifact_id)
    if artifact is None or not artifact.durable:
        raise HTTPException(404, "Artifact not found")
    text_preview = None
    truncated = False
    if (
        artifact.media_type.startswith("text/")
        or artifact.media_type == "application/json"
    ):
        try:
            storage = get_storage()
            if not storage.verify(
                artifact.storage_key, artifact.sha256, artifact.byte_size
            ):
                raise HTTPException(409, "Artifact integrity check failed")
            content = storage.read(
                artifact.storage_key, get_settings().max_upload_bytes
            ).decode("utf-8", errors="replace")
            if contains_secret(content):
                raise HTTPException(403, "Artifact contains configured credentials")
            text_preview = content[:max_characters]
            truncated = len(content) > max_characters
        except (OSError, ValueError, StorageUnavailable) as exc:
            raise HTTPException(503, "Artifact bytes are unavailable") from exc
    return {
        "id": artifact.id,
        "display_name": artifact.display_name,
        "media_type": artifact.media_type,
        "byte_size": artifact.byte_size,
        "text": text_preview,
        "truncated": truncated,
    }
