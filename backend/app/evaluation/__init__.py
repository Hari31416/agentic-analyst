"""Evaluation contracts and deterministic metric adapters."""

from .contracts import (
    EvaluationArtifact,
    EvaluationCase,
    EvaluationExpectations,
    EvaluationOutcome,
    EvaluationPassage,
    EvaluationReview,
    EvaluationSource,
    ExpectedArtifact,
    ExpectedCalculation,
    ExpectedPassage,
    MetricResult,
)
from .metrics import (
    character_error_rate,
    retrieval_metrics,
    score_case,
    table_accuracy,
    word_error_rate,
)

__all__ = [
    "EvaluationArtifact",
    "EvaluationCase",
    "EvaluationExpectations",
    "EvaluationOutcome",
    "EvaluationPassage",
    "EvaluationReview",
    "EvaluationSource",
    "ExpectedArtifact",
    "ExpectedCalculation",
    "ExpectedPassage",
    "MetricResult",
    "character_error_rate",
    "retrieval_metrics",
    "score_case",
    "table_accuracy",
    "word_error_rate",
]
