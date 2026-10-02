from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


Json = JSON().with_variant(JSONB(), "postgresql")


class Identity:
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Workspace(Identity, Base):
    __tablename__ = "workspaces"
    label: Mapped[str] = mapped_column(String(120))


class Thread(Identity, Base):
    __tablename__ = "threads"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    label: Mapped[str] = mapped_column(String(120))


class Source(Identity, Base):
    __tablename__ = "sources"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(Integer, default=1)
    display_name: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(30), default="uploaded")
    storage_key: Mapped[str | None] = mapped_column(String(512))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    schema_version: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)

    @property
    def description(self) -> str | None:
        value = self.details.get("description")
        return value if isinstance(value, str) else None

    @property
    def metric_hints(self) -> dict[str, str]:
        value = self.details.get("metric_hints", {})
        return (
            {str(k): str(v) for k, v in value.items()}
            if isinstance(value, dict)
            else {}
        )


class Connection(Identity, Base):
    __tablename__ = "connections"
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), unique=True)
    dialect: Mapped[str] = mapped_column(String(20))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    database_name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(String(255))
    encrypted_credentials: Mapped[str] = mapped_column(Text)
    options: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)


class Dataset(Identity, Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("source_id", "source_version", "identity"),)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    source_version: Mapped[int] = mapped_column(Integer)
    identity: Mapped[str] = mapped_column(String(512))
    schema_version: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    storage_key: Mapped[str | None] = mapped_column(String(512))
    designation: Mapped[str] = mapped_column(String(20), default="original")
    lineage: Mapped[list[str]] = mapped_column(Json, default=list)


class Run(Identity, Base):
    __tablename__ = "runs"
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"), index=True)
    state: Mapped[str] = mapped_column(String(30), default="queued")
    selected_source_ids: Mapped[list[str]] = mapped_column(Json, default=list)
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(Json)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Identity, Base):
    __tablename__ = "messages"
    thread_id: Mapped[str] = mapped_column(ForeignKey("threads.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    selected_source_ids: Mapped[list[str]] = mapped_column(Json, default=list)
    references: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)


class Job(Identity, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_dispatch", "state", "available_at"),)
    kind: Mapped[str] = mapped_column(String(40))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(120), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    state: Mapped[str] = mapped_column(String(30), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(Json)


class Event(Identity, Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)


class ToolCall(Identity, Base):
    __tablename__ = "tool_calls"
    __table_args__ = (UniqueConstraint("run_id", "provider_call_id"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    provider_call_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(80))
    input_reference: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    decision: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30))
    result: Mapped[dict[str, Any] | None] = mapped_column(Json)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(Identity, Base):
    __tablename__ = "artifacts"
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    tool_call_id: Mapped[str | None] = mapped_column(ForeignKey("tool_calls.id"))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(120))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    lineage: Mapped[list[str]] = mapped_column(Json, default=list)
    durable: Mapped[bool] = mapped_column(default=True)


class Evidence(Identity, Base):
    __tablename__ = "evidence"
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    source_ids: Mapped[list[str]] = mapped_column(Json)
    details: Mapped[dict[str, Any]] = mapped_column(Json)


class AuditEvent(Identity, Base):
    __tablename__ = "audit_events"
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), index=True)
    tool_call_id: Mapped[str | None] = mapped_column(ForeignKey("tool_calls.id"))
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id"))
    action: Mapped[str] = mapped_column(String(80))
    decision: Mapped[str] = mapped_column(String(30))
    reason_code: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
