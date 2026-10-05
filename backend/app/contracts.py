from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CLARIFICATION = "awaiting_clarification"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXHAUSTED = "budget_exhausted"


TERMINAL_STATES = {
    RunState.CLARIFICATION,
    RunState.COMPLETED,
    RunState.FAILED,
    RunState.CANCELLED,
    RunState.EXHAUSTED,
}


class SafeError(Contract):
    code: str
    message: str = Field(max_length=500)
    retryable: bool = False


class ToolResult(Contract):
    status: Literal["ok", "partial", "rejected", "failed"]
    summary: str = Field(max_length=8000)
    evidence_ids: list[UUID] = Field(default_factory=list)
    artifact_ids: list[UUID] = Field(default_factory=list)
    error: SafeError | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class ArtifactRole(StrEnum):
    OUTPUT = "output"
    INTERMEDIATE = "intermediate"
    EXECUTION_CODE = "execution_code"
    INPUT_SNAPSHOT = "input_snapshot"
    METADATA = "metadata"


class FinalAnswer(Contract):
    text: str
    evidence_ids: list[UUID] = Field(default_factory=list)
    artifact_ids: list[UUID] = Field(default_factory=list)
    output_artifact_ids: list[UUID] = Field(
        default_factory=list,
        max_length=20,
        description="Deliverables the user should receive, also declared in artifact_ids. Exclude execution code, input snapshots and metadata.",
    )
    clarification: bool = False


class RunEvent(Contract):
    schema_version: int = 1
    id: UUID
    run_id: UUID
    sequence: int = Field(ge=1)
    created_at: datetime
    type: str
    payload: dict[str, Any]


class ArtifactInfo(Contract):
    role: ArtifactRole = ArtifactRole.INTERMEDIATE
    id: UUID
    storage_key: str
    media_type: str
    byte_size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    run_id: UUID | None
    tool_call_id: UUID | None
    lineage: list[str] = Field(default_factory=list)
    durable: bool = True


class DocumentEvidence(Contract):
    kind: Literal["document"] = "document"
    source_id: UUID
    source_version: int = Field(ge=1)
    chunk_id: UUID | None = None
    location: dict[str, Any]
    excerpt: str
    retrieval: dict[str, Any] = Field(default_factory=dict)
    translation_reference: str | None = None


class StructuredEvidence(Contract):
    kind: Literal["structured"] = "structured"
    source_id: UUID
    source_version: int = Field(ge=1)
    schema_version: str
    query: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    code_artifact_id: UUID | None = None
    executed_at: datetime
    result_artifact_id: UUID
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    row_limit: int = Field(ge=1)
    units: dict[str, str] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)


class CreateLabel(Contract):
    label: str = Field(min_length=1, max_length=120)

    @field_validator("label")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("label cannot be blank")
        return value.strip()


class WorkspaceView(Contract):
    id: str
    label: str
    created_at: datetime


class ThreadView(WorkspaceView):
    workspace_id: str


class SourceView(Contract):
    description: str | None = None
    metric_hints: dict[str, str] = Field(default_factory=dict)
    id: str
    display_name: str
    kind: str
    state: str
    version: int
