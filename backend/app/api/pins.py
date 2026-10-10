"""Owner-scoped CRUD for thread, message/turn, and artifact bookmarks."""

from datetime import timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.artifacts import manifest
from app.api.resource_lifecycle import lock_thread
from app.audit.redaction import contains_secret, redact
from app.auth.security import require_auth
from app.db.models import Artifact, Message, Pin, Run, Thread, User, Workspace, now
from app.db.session import get_session

router = APIRouter(prefix="/api", tags=["pins"])
Db = Annotated[Session, Depends(get_session)]
Owner = Annotated[User, Depends(require_auth)]
Kind = Literal["thread", "message", "artifact"]


class PinFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title cannot be blank")
        return value

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str]) -> list[str]:
        tags = list(dict.fromkeys(tag.strip() for tag in value if tag.strip()))
        if any(len(tag) > 80 for tag in tags):
            raise ValueError("Tags must be at most 80 characters")
        return tags


class PinCreate(PinFields):
    kind: Kind
    target_id: UUID


class PinUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    tags: list[str] | None = Field(default=None, max_length=30)


def view(pin: Pin, thread: Thread) -> dict[str, Any]:
    return {
        "id": pin.id,
        "kind": pin.kind,
        "target_id": pin.target_id,
        "workspace_id": thread.workspace_id,
        "thread_id": thread.id,
        "thread_label": redact(thread.label),
        "title": redact(pin.title),
        "notes": redact(pin.notes),
        "tags": redact(pin.tags),
        "created_at": (
            pin.created_at.replace(tzinfo=timezone.utc)
            if pin.created_at.tzinfo is None
            else pin.created_at
        ),
        "updated_at": (
            pin.updated_at.replace(tzinfo=timezone.utc)
            if pin.updated_at.tzinfo is None
            else pin.updated_at
        ),
    }


def owned(session: Session, pin_id: str, user: User) -> tuple[Pin, Thread]:
    row = session.execute(
        select(Pin, Thread)
        .join(Thread, Pin.thread_id == Thread.id)
        .where(Pin.id == pin_id, Pin.user_id == user.id)
    ).first()
    if row is None:
        raise HTTPException(404, "Pin not found")
    return row[0], row[1]


def check_fields(body: PinFields) -> None:
    if contains_secret(body.model_dump_json()):
        raise HTTPException(422, "Pin contains configured credentials")


@router.get("/workspaces/{workspace_id}/pins")
def list_pins(
    workspace_id: UUID,
    session: Db,
    user: Owner,
    kind: Kind | None = None,
    target_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> list[dict[str, Any]]:
    if session.get(Workspace, str(workspace_id)) is None:
        raise HTTPException(404, "Workspace not found")
    query = (
        select(Pin, Thread)
        .join(Thread, Pin.thread_id == Thread.id)
        .where(Pin.user_id == user.id, Thread.workspace_id == str(workspace_id))
    )
    if kind is not None:
        query = query.where(Pin.kind == kind)
    if target_id is not None:
        query = query.where(Pin.target_id == str(target_id))
    rows = session.execute(
        query.order_by(Pin.created_at.desc(), Pin.id).offset(offset).limit(limit)
    )
    return [view(pin, thread) for pin, thread in rows]


@router.post("/workspaces/{workspace_id}/pins", status_code=201)
def create_pin(
    workspace_id: UUID, body: PinCreate, session: Db, user: Owner
) -> dict[str, Any]:
    check_fields(body)
    target_id = str(body.target_id)
    message = session.get(Message, target_id) if body.kind == "message" else None
    artifact = session.get(Artifact, target_id) if body.kind == "artifact" else None
    if body.kind == "thread":
        thread_id = target_id
    elif message is not None:
        thread_id = message.thread_id
    elif artifact is not None and artifact.durable and artifact.run_id:
        run = session.get(Run, artifact.run_id)
        if run is None:
            raise HTTPException(404, "Artifact run not found")
        thread_id = run.thread_id
    else:
        raise HTTPException(404, "Pin target not found")
    thread = lock_thread(session, thread_id)
    if thread.workspace_id != str(workspace_id):
        raise HTTPException(404, "Pin target not found in this workspace")
    # Parent locks serialize pin creation with resource deletion and other creates.
    if (
        message is not None
        and session.get(Message, target_id, populate_existing=True) is None
    ):
        raise HTTPException(404, "Message not found")
    if artifact is not None:
        refreshed = session.get(Artifact, target_id, populate_existing=True)
        if refreshed is None or not refreshed.durable:
            raise HTTPException(404, "Artifact not found")
    if session.scalar(
        select(Pin.id).where(
            Pin.user_id == user.id, Pin.kind == body.kind, Pin.target_id == target_id
        )
    ):
        raise HTTPException(409, "This item is already pinned")
    pin = Pin(
        user_id=user.id,
        thread_id=thread.id,
        kind=body.kind,
        target_id=target_id,
        message_id=target_id if message is not None else None,
        artifact_id=target_id if artifact is not None else None,
        title=body.title,
        notes=body.notes,
        tags=body.tags,
    )
    session.add(pin)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(409, "Pin target changed or is already pinned") from error
    return view(pin, thread)


@router.get("/pins/{pin_id}")
def read_pin(pin_id: UUID, session: Db, user: Owner) -> dict[str, Any]:
    pin, thread = owned(session, str(pin_id), user)
    result = view(pin, thread)
    if pin.kind == "message":
        message = session.get(Message, pin.message_id)
        if message is None:
            raise HTTPException(404, "Pinned message not found")
        query = select(Message).where(Message.id == message.id)
        # A run groups the user's question with the assistant's answer.
        if message.run_id:
            query = select(Message).where(
                Message.thread_id == thread.id, Message.run_id == message.run_id
            )
        result["messages"] = [
            {
                "id": item.id,
                "role": item.role,
                "content": redact(item.content),
                "references": redact(item.references),
            }
            for item in session.scalars(query.order_by(Message.created_at, Message.id))
        ]
    elif pin.kind == "artifact":
        artifact = session.get(Artifact, pin.artifact_id)
        if artifact is None or not artifact.durable:
            raise HTTPException(404, "Pinned artifact is unavailable")
        result["artifact"] = manifest(artifact)
    return result


@router.patch("/pins/{pin_id}")
def update_pin(
    pin_id: UUID, body: PinUpdate, session: Db, user: Owner
) -> dict[str, Any]:
    pin, thread = owned(session, str(pin_id), user)
    changes = body.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise HTTPException(422, "Pin fields cannot be null")
    # Validate the merged record with exactly the same bounds and normalization.
    try:
        fields = PinFields.model_validate(
            {"title": pin.title, "notes": pin.notes, "tags": pin.tags, **changes}
        )
    except ValueError as error:
        raise HTTPException(422, "Invalid pin title, notes, or tags") from error
    check_fields(fields)
    pin.title, pin.notes, pin.tags = fields.title, fields.notes, fields.tags
    pin.updated_at = now()
    session.commit()
    return view(pin, thread)


@router.delete("/pins/{pin_id}", status_code=204)
def delete_pin(pin_id: UUID, session: Db, user: Owner) -> None:
    pin, _ = owned(session, str(pin_id), user)
    session.delete(pin)
    session.commit()
