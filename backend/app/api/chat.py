import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.contracts import Contract, RunEvent, RunState, TERMINAL_STATES
from app.db.models import Artifact, Job, Message, Run, Source, Thread, now
from app.db.repository import append_event, audit, events_after
from app.db.session import factory, get_session
from app.language.metadata import LanguageMetadata
from app.storage.factory import get_storage
from app.storage.s3 import StorageUnavailable

router = APIRouter(prefix="/api")
Db = Annotated[Session, Depends(get_session)]


class RunRequest(Contract):
    text: str = Field(min_length=1, max_length=20000)
    selected_source_ids: list[UUID] = Field(default_factory=list, max_length=100)
    answer_language: str = "en-IN"
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
        "outcome": run.outcome,
        "selected_source_ids": run.selected_source_ids,
        "answer_language": run.config.get("answer_language", "en-IN"),
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
            "content": row.content,
            "run_id": row.run_id,
            "references": row.references,
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
    if not settings.model_configured:
        raise HTTPException(
            503,
            "Configure OPENAI_BASE_URL, OPENAI_API_KEY, and OPENAI_MODEL before starting chat",
        )
    if body.answer_language not in settings.supported_languages:
        raise HTTPException(422, "Requested answer language is unavailable")
    thread = session.scalar(
        select(Thread).where(Thread.id == thread_id).with_for_update()
    )
    if thread is None:
        raise HTTPException(404, "Thread not found")
    existing = session.get(Run, str(body.request_id))
    if existing:
        original = session.scalar(
            select(Message).where(Message.run_id == existing.id, Message.role == "user")
        )
        if (
            existing.thread_id != thread_id
            or original is None
            or original.content != body.text
            or existing.selected_source_ids
            != [str(i) for i in body.selected_source_ids]
            or existing.config.get("answer_language") != body.answer_language
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
    selected = list(dict.fromkeys(str(i) for i in body.selected_source_ids))
    source_rows = (
        list(
            session.scalars(
                select(Source).where(
                    Source.id.in_(selected), Source.workspace_id == thread.workspace_id
                )
            )
        )
        if selected
        else []
    )
    if len(source_rows) != len(selected):
        raise HTTPException(422, "Selected sources must belong to this workspace")
    run = Run(
        id=str(body.request_id),
        thread_id=thread_id,
        selected_source_ids=selected,
        config={
            "answer_language": body.answer_language,
            "model": settings.openai_model,
            "prompt_version": "analyst-v1",
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


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: str, session: Db) -> dict[str, Any]:
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
        while not await request.is_disconnected():
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
    except (OSError, ValueError, StorageUnavailable) as exc:
        raise HTTPException(503, "Artifact bytes are unavailable") from exc
    return Response(
        content,
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(artifact.display_name, safe='')}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
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
