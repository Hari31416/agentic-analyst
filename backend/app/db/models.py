from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text as sql_text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]

from app.contracts import ArtifactRole


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


class User(Identity, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(10), default="user")
    is_active: Mapped[bool] = mapped_column(default=True)
    token_version: Mapped[int] = mapped_column(default=0)


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"
    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Thread(Identity, Base):
    __tablename__ = "threads"
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(120))


class Source(Identity, Base):
    __tablename__ = "sources"
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
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
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), unique=True
    )
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
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    source_version: Mapped[int] = mapped_column(Integer)
    identity: Mapped[str] = mapped_column(String(512))
    schema_version: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    storage_key: Mapped[str | None] = mapped_column(String(512))
    designation: Mapped[str] = mapped_column(String(20), default="original")
    lineage: Mapped[list[str]] = mapped_column(Json, default=list)


class Run(Identity, Base):
    __tablename__ = "runs"
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(String(30), default="queued")
    selected_source_ids: Mapped[list[str]] = mapped_column(Json, default=list)
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    outcome: Mapped[dict[str, Any] | None] = mapped_column(Json)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Identity, Base):
    __tablename__ = "messages"
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    selected_source_ids: Mapped[list[str]] = mapped_column(Json, default=list)
    references: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)


class Pin(Identity, Base):
    """Personal bookmarks referencing live resources, never copied artifacts."""

    __tablename__ = "pins"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "target_id", name="uq_pins_owner_target"),
        CheckConstraint(
            "(kind = 'thread' AND target_id = thread_id AND message_id IS NULL AND artifact_id IS NULL) OR "
            "(kind = 'message' AND target_id = message_id AND message_id IS NOT NULL AND artifact_id IS NULL) OR "
            "(kind = 'artifact' AND target_id = artifact_id AND artifact_id IS NOT NULL AND message_id IS NULL)",
            name="ck_pins_target",
        ),
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE")
    )
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(Json, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Identity, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_dispatch", "state", "available_at"),)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(40))
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
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
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)


class ToolCall(Identity, Base):
    __tablename__ = "tool_calls"
    __table_args__ = (UniqueConstraint("run_id", "provider_call_id"),)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
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
    __table_args__ = (
        CheckConstraint(
            "role IN ('output', 'intermediate', 'execution_code', 'input_snapshot', 'metadata')",
            name="ck_artifacts_role",
        ),
    )
    role: Mapped[str] = mapped_column(
        String(30),
        default=ArtifactRole.INTERMEDIATE.value,
        server_default=ArtifactRole.INTERMEDIATE.value,
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    tool_call_id: Mapped[str | None] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="CASCADE")
    )
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    display_name: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(120))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    lineage: Mapped[list[str]] = mapped_column(Json, default=list)
    durable: Mapped[bool] = mapped_column(default=True)


class Evidence(Identity, Base):
    __tablename__ = "evidence"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    source_ids: Mapped[list[str]] = mapped_column(Json)
    details: Mapped[dict[str, Any]] = mapped_column(Json)


class AuditEvent(Identity, Base):
    __tablename__ = "audit_events"
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    tool_call_id: Mapped[str | None] = mapped_column(
        ForeignKey("tool_calls.id", ondelete="CASCADE")
    )
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE")
    )
    action: Mapped[str] = mapped_column(String(80))
    decision: Mapped[str] = mapped_column(String(30))
    reason_code: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)


class Document(Identity, Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("source_id", "source_version"),)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    source_version: Mapped[int] = mapped_column(Integer)
    extractor_version: Mapped[str] = mapped_column(String(120))
    chunker_version: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(30), default="queued")
    stage: Mapped[str] = mapped_column(String(40), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    index_generation_id: Mapped[str | None] = mapped_column(String(36))


class DocumentBlock(Identity, Base):
    __tablename__ = "document_blocks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal"),)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    heading: Mapped[str | None] = mapped_column(Text)
    location: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    language: Mapped[str] = mapped_column(String(20))
    scripts: Mapped[list[str]] = mapped_column(Json, default=list)


class DocumentChunk(Identity, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunker_version", "ordinal"),
        Index(
            "ix_document_chunks_english_fts",
            sql_text(
                "to_tsvector('english'::regconfig, (coalesce(heading, '') || ' ') || normalized_text)"
            ),
            postgresql_using="gin",
            postgresql_where=sql_text("language ILIKE 'en%'"),
        ).ddl_if(dialect="postgresql"),
        Index(
            "ix_document_chunks_simple_fts",
            sql_text(
                "to_tsvector('simple'::regconfig, (coalesce(heading, '') || ' ') || normalized_text)"
            ),
            postgresql_using="gin",
            postgresql_where=sql_text("language NOT ILIKE 'en%'"),
        ).ddl_if(dialect="postgresql"),
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    chunker_version: Mapped[str] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    heading: Mapped[str | None] = mapped_column(Text)
    location: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    language: Mapped[str] = mapped_column(String(20))
    block_ids: Mapped[list[str]] = mapped_column(Json, default=list)
    token_count: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[str | None] = mapped_column(String(36))
    previous_id: Mapped[str | None] = mapped_column(String(36))
    next_id: Mapped[str | None] = mapped_column(String(36))


class SummaryCache(Identity, Base):
    """Versioned deterministic summaries with their chunk-level provenance."""

    __tablename__ = "summary_cache"
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    scope: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)


class IndexGeneration(Identity, Base):
    __tablename__ = "index_generations"
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    source_version: Mapped[int] = mapped_column(Integer)
    content_sha256: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(120))
    chunker_version: Mapped[str] = mapped_column(String(120))
    model_id: Mapped[str] = mapped_column(String(255))
    dimensions: Mapped[int] = mapped_column(Integer)
    vector_metric: Mapped[str] = mapped_column(String(30), default="cosine")
    status: Mapped[str] = mapped_column(String(30), default="building")
    config_json: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChunkEmbedding(Identity, Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (UniqueConstraint("generation_id", "chunk_id"),)
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("index_generations.id", ondelete="CASCADE"), index=True
    )
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), index=True
    )
    embedding: Mapped[Any] = mapped_column(Vector())
