"""Small deterministic evaluation metrics, independent of model providers."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .contracts import EvaluationCase, MetricResult


def _result(name: str, passed: bool, **details: Any) -> MetricResult:
    return MetricResult(
        name=name,
        status="pass" if passed else "fail",
        score=1.0 if passed else 0.0,
        details=details,
    )


def _normalise_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _flatten(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for child in value.values():
            yield from _flatten(child)
    elif isinstance(value, list):
        for child in value:
            yield from _flatten(child)
    elif value is not None:
        yield str(value)


def _schema_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _schema_matches(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) >= len(expected)
            and all(
                _schema_matches(actual[i], value) for i, value in enumerate(expected)
            )
        )
    return actual == expected


def score_case(case: EvaluationCase, outcome: dict[str, Any]) -> list[MetricResult]:
    """Score stable known-answer contracts from a public run and its audit evidence."""
    run_state = str(
        outcome.get("run_state", outcome.get("status", "unknown"))
    ).casefold()
    answer = str(outcome.get("answer_text", outcome.get("answer", "")) or "")
    if run_state in {
        "failed",
        "timeout",
        "cancelled",
        "infrastructure_error",
        "model_error",
        "error",
    }:
        return [
            MetricResult(
                name="run_completion",
                status="needs_review",
                details={"run_state": run_state},
            )
        ]

    metrics: list[MetricResult] = []
    if case.answerability == "unsupported":
        clarified = bool(outcome.get("clarification"))
        metrics.append(
            _result("unsupported_handling", clarified, clarification_provided=clarified)
        )
    elif case.answerability == "ambiguous":
        clarified = bool(outcome.get("clarification"))
        metrics.append(
            _result("ambiguous_handling", clarified, clarification_provided=clarified)
        )

    calculations = (
        outcome.get("calculations", {}) or outcome.get("structured_results", {}) or {}
    )
    for expected in case.expectations.calculations:
        actual = (
            calculations.get(expected.key) if isinstance(calculations, dict) else None
        )
        try:
            actual_value = (
                Decimal(str(actual.get("value"))) if isinstance(actual, dict) else None
            )
            actual_unit = (
                str(actual.get("unit", "")).strip() if isinstance(actual, dict) else ""
            )
            matches_value = (
                actual_value is not None
                and abs(actual_value - expected.value) <= expected.tolerance
            )
        except (InvalidOperation, TypeError, ValueError):
            actual_value, matches_value, actual_unit = None, False, ""
        matches = matches_value and actual_unit.casefold() == expected.unit.casefold()
        if expected.column:
            matches = (
                matches
                and str(actual.get("column", "")).casefold()
                == expected.column.casefold()
            )
        metrics.append(
            _result(
                "calculation:" + expected.key,
                matches,
                expected_value=str(expected.value),
                actual_value=str(actual_value) if actual_value is not None else None,
                expected_unit=expected.unit,
                actual_unit=actual_unit,
            )
        )

    evidence = outcome.get("evidence", outcome.get("passages", [])) or []
    for index, expected in enumerate(case.expectations.passages):
        found = False
        for item in evidence:
            if isinstance(item, dict):
                alias = item.get("source_alias") or item.get("alias")
                text = (
                    item.get("text") or item.get("content") or item.get("passage") or ""
                )
                # Audit evidence commonly uses source_id; resolve it through runner's alias map.
                if not alias and item.get("source_id"):
                    alias = next(
                        (
                            a
                            for a, sid in (
                                outcome.get("source_aliases", {}) or {}
                            ).items()
                            if sid == item["source_id"]
                        ),
                        None,
                    )
            else:
                alias, text = getattr(item, "source_alias", None), getattr(
                    item, "text", ""
                )
            if alias == expected.source_alias and _normalise_text(
                expected.contains
            ) in _normalise_text(str(text)):
                found = True
                break
        metrics.append(
            _result(
                f"passage:{index + 1}",
                found,
                source_alias=expected.source_alias,
                expected_phrase=expected.contains,
            )
        )

    artifacts = outcome.get("artifacts", []) or []
    for index, expected in enumerate(case.expectations.artifacts):
        found = False
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            media_type = artifact.get("media_type") or artifact.get("mime_type")
            schema = artifact.get("schema") or artifact.get("metadata", {})
            exists = artifact.get("exists", artifact.get("downloadable", True))
            if (
                exists
                and media_type == expected.media_type
                and _schema_matches(schema, expected.schema)
            ):
                found = True
                break
        metrics.append(
            _result(
                f"artifact:{index + 1}",
                found,
                media_type=expected.media_type,
                schema=expected.schema,
            )
        )

    calls = outcome.get("tool_calls", []) or []
    called_actions = []
    for call in calls:
        action = (
            call.get("action") or call.get("name")
            if isinstance(call, dict)
            else str(call)
        )
        called_actions.append(str(action))
    unexpected = sorted(set(called_actions) - set(case.expectations.allowed_actions))
    if calls or case.expectations.allowed_actions:
        metrics.append(
            _result(
                "action_allowlist",
                not unexpected,
                unexpected_actions=unexpected,
                called_actions=called_actions,
            )
        )
    if outcome.get("prohibited_action_rejected") is not None:
        metrics.append(
            _result(
                "prohibited_action_rejection",
                bool(outcome["prohibited_action_rejected"]),
            )
        )

    hashes = outcome.get("source_hashes", {}) or {}
    for source in case.sources:
        before = source.sha256
        source_observation = (
            hashes.get(source.alias, {}) if isinstance(hashes, dict) else {}
        )
        if isinstance(source_observation, dict):
            before = source_observation.get("before", before)
            after = source_observation.get("after")
        else:
            after = source_observation
        if before is not None or after is not None:
            metrics.append(
                _result(
                    f"source_immutable:{source.alias}",
                    bool(before and after and before == after),
                    before=before,
                    after=after,
                )
            )

    if not metrics:
        metrics.append(
            MetricResult(
                name="known_answer",
                status="needs_review",
                details={"reason": "case has no deterministic expectations"},
            )
        )
    if case.review.provenance != "human_reviewed":
        metrics.append(
            MetricResult(
                name="label_provenance",
                status="needs_review",
                details={"provenance": case.review.provenance},
            )
        )
    return metrics


def _edit_distance(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(
                min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b))
            )
        previous = current
    return previous[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    """Unicode code-point CER; empty reference is 0 only for empty hypothesis."""
    ref = list(reference)
    return (
        _edit_distance(ref, list(hypothesis)) / len(ref)
        if ref
        else (0.0 if not hypothesis else 1.0)
    )


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Whitespace-token WER, retaining native-script tokens without transliteration."""
    ref = reference.split()
    return (
        _edit_distance(ref, hypothesis.split()) / len(ref)
        if ref
        else (0.0 if not hypothesis else 1.0)
    )


def retrieval_metrics(
    relevant: set[str], ranked: list[str], *, k: int | None = None
) -> dict[str, float]:
    """Precision, recall, reciprocal rank and hit rate for stable passage IDs."""
    ranked = ranked[:k] if k is not None else ranked
    hits = [item for item in ranked if item in relevant]
    first = next((i for i, item in enumerate(ranked, 1) if item in relevant), None)
    return {
        "precision": len(hits) / len(ranked) if ranked else 0.0,
        "recall": len(set(hits)) / len(relevant) if relevant else 0.0,
        "reciprocal_rank": 1.0 / first if first else 0.0,
        "hit_rate": 1.0 if first else 0.0,
    }


def table_accuracy(expected: list[list[Any]], actual: list[list[Any]]) -> float:
    """Exact cell accuracy for aligned table fixtures, with shape mismatches penalized."""
    total = sum(len(row) for row in expected)
    if total == 0:
        return 1.0 if not actual else 0.0
    correct = sum(
        1
        for i, row in enumerate(expected)
        for j, value in enumerate(row)
        if i < len(actual) and j < len(actual[i]) and actual[i][j] == value
    )
    actual_cells = sum(len(row) for row in actual)
    return correct / max(total, actual_cells)
