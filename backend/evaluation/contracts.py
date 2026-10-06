"""Versioned, deterministic evaluation case and outcome contracts."""

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EvaluationSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=40)
    path: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    version: str | None = None


class ExpectedCalculation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=120)
    value: Decimal
    unit: str | None = Field(default=None, min_length=1, max_length=80)
    tolerance: Decimal = Field(default=Decimal("0"), ge=0)
    column: str | None = None


class ExpectedPassage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_alias: str = Field(min_length=1, max_length=80)
    contains: str = Field(min_length=1, max_length=1000)
    # Alternatives are source-backed anchors, not semantic entailment checks.
    contains_any: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        default_factory=list, max_length=20
    )
    match_policy: Literal["required", "diagnostic"] = "required"


class ExpectedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: str = Field(min_length=1, max_length=120)
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")


class EvaluationExpectations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calculations: list[ExpectedCalculation] = Field(default_factory=list)
    passages: list[ExpectedPassage] = Field(default_factory=list)
    artifacts: list[ExpectedArtifact] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    rubric: dict[str, Any] = Field(default_factory=dict)


class EvaluationReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provenance: Literal["synthetic", "unreviewed", "human_reviewed"] = "unreviewed"
    reviewer: str | None = None
    reviewed_at: str | None = None
    requires_human_review: bool = False


class EvaluationCase(BaseModel):
    """A portable evaluation case; schema_version is bumped on breaking changes."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=10000)
    language: str = Field(min_length=2, max_length=40)
    # The language of the prompt and the requested answer language can differ,
    # especially for Romanized Hindi prompts. None preserves legacy behavior.
    answer_language: str | None = Field(default=None, min_length=2, max_length=40)
    tags: list[str] = Field(default_factory=list)
    answerability: Literal["answerable", "ambiguous", "unsupported"]
    sources: list[EvaluationSource] = Field(default_factory=list)
    expectations: EvaluationExpectations = Field(default_factory=EvaluationExpectations)
    review: EvaluationReview = Field(default_factory=EvaluationReview)


class EvaluationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_type: str
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    exists: bool = True


class EvaluationPassage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_alias: str
    text: str


class EvaluationToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    rejected: bool = False


class EvaluationOutcome(BaseModel):
    """Public-run observations consumed by deterministic scoring."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "completed",
        "failed",
        "timeout",
        "cancelled",
        "infrastructure_error",
        "model_error",
    ]
    answer: str = ""
    calculations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    passages: list[EvaluationPassage] = Field(default_factory=list)
    artifacts: list[EvaluationArtifact] = Field(default_factory=list)
    tool_calls: list[EvaluationToolCall] = Field(default_factory=list)
    source_hashes: dict[str, str] = Field(default_factory=dict)
    prohibited_action_rejected: bool | None = None


class MetricResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: Literal["pass", "fail", "not_applicable", "needs_review"]
    score: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CaseScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: Literal["pass", "fail", "not_applicable", "needs_review"]
    metrics: list[MetricResult]
