"""Small deterministic evaluation metrics, independent of model providers."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
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


def unit_present(unit: str, text: str) -> bool:
    """Accept an explicit currency code or its unambiguous symbol."""
    symbols = {"GBP": "£", "INR": "₹", "EUR": "€"}
    return bool(re.search(r"(?<!\w)" + re.escape(unit) + r"(?!\w)", text, re.I)) or (
        unit.upper() in symbols and symbols[unit.upper()] in text
    )


def numeric_claims(answer: str) -> list[Decimal]:
    """Read numeric literals, preserving decimals and protected digit grouping."""
    text = re.sub(r"\[(?:evidence|artifact):[^\]]+\]", "", answer)
    # Only remove typographic grouping inside full three-digit groups. Ordinary
    # whitespace separates values; never turn '22 34' into 2234.
    text = re.sub(
        r"(?<![\w\d])[+-]?(?:\d{1,3}(?:[\u00a0\u202f\u2009]\d{3})+"
        r"|\d{1,2}(?:[\u00a0\u202f\u2009]\d{2})+[\u00a0\u202f\u2009]\d{3})(?!\d)",
        lambda match: re.sub(r"[\u00a0\u202f\u2009]", "", match.group()),
        text,
    )
    claims = []
    for match in re.findall(r"(?<![\w/-])-?\d[\d,]*(?:\.\d+)?(?![\w/-])", text):
        try:
            claims.append(Decimal(match.replace(",", "")))
        except InvalidOperation:
            pass
    return claims


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
    return bool(actual == expected)


def _observation_rows(
    outcome: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Prefer declared calculation evidence, then the latest legacy result."""
    eligible: list[dict[str, Any]] = []
    declared = set(outcome.get("declared_evidence_ids") or [])
    has_evidence_links = False
    linked: list[dict[str, Any]] = []
    for call in outcome.get("tool_calls", []) or []:
        if not isinstance(call, dict):
            continue
        if call.get("name", call.get("action")) not in {"run_sql", "analyze_data"}:
            continue
        result = call.get("result")
        if not isinstance(result, dict):
            continue
        state = str(call.get("status", result.get("status", ""))).casefold()
        if state not in {"ok", "success", "succeeded", "completed"}:
            continue
        eligible.append(result)
        evidence_ids = set(result.get("evidence_ids") or [])
        evidence_ids.update(
            item["id"]
            for item in outcome.get("evidence", []) or []
            if isinstance(item, dict)
            and item.get("id")
            and call.get("id")
            and item.get("tool_call_id") == call["id"]
        )
        has_evidence_links = has_evidence_links or bool(evidence_ids)
        if declared.intersection(evidence_ids):
            linked.append(result)
    if "declared_evidence_ids" in outcome and has_evidence_links:
        eligible = linked
    if not eligible:
        return []
    # Never choose an earlier result merely because its values happen to match gold.
    # Linked final evidence survives a later supplementary query; older observations
    # without links retain the latest-successful-result behavior.
    result = eligible[-1]
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for container in (result, result.get("data"), result.get("result")):
        if not isinstance(container, dict):
            continue
        possible = container.get("rows", container.get("preview"))
        if isinstance(possible, list):
            rows.extend((row, container) for row in possible if isinstance(row, dict))
        analysis = container.get("analysis")
        if isinstance(analysis, dict):
            analysis_rows = analysis.get("rows", analysis.get("preview"))
            if isinstance(analysis_rows, list):
                rows.extend(
                    (row, analysis) for row in analysis_rows if isinstance(row, dict)
                )
    return rows


def _observed_calculation(
    case: EvaluationCase, outcome: dict[str, Any], key: str, column: str | None
) -> dict[str, Any] | None:
    """Find an explicitly returned aggregate; never infer a value from prose or SQL."""
    calculations = (
        outcome.get("calculations", {}) or outcome.get("structured_results", {}) or {}
    )
    explicit = calculations.get(key) if isinstance(calculations, dict) else None
    if isinstance(explicit, dict):
        return explicit
    aliases = outcome.get("source_aliases", {}) or {}
    source_ids = {
        aliases[source.alias] for source in case.sources if source.alias in aliases
    }
    for evidence in outcome.get("evidence", []) or []:
        if not isinstance(evidence, dict):
            continue
        if "declared_evidence_ids" in outcome and evidence.get("id") not in set(
            outcome.get("declared_evidence_ids") or []
        ):
            continue
        if source_ids and not source_ids.intersection(evidence.get("source_ids", [])):
            continue
        details = evidence.get("details")
        if isinstance(details, dict):
            candidate = details.get(key)
            units = details.get("units") or {}
            if candidate is None and column and column in units:
                candidate = details.get("value")
            if candidate is not None:
                return {
                    "value": candidate,
                    "unit": units.get(column or key, ""),
                    "column": column or key,
                }
    for row, metadata in _observation_rows(outcome):
        # A label/key can identify a scalar in the result; the optional column narrows row selection.
        candidates = [key, column] if column else [key]
        for candidate in candidates:
            if candidate and candidate in row:
                unit = (
                    (metadata.get("units") or {}).get(candidate, "")
                    if isinstance(metadata, dict)
                    else ""
                )
                if not unit and column:
                    unit = _source_column_unit(case, outcome, column)
                return {"value": row[candidate], "unit": unit, "column": candidate}
    return None


def _source_column_unit(
    case: EvaluationCase, outcome: dict[str, Any], column: str
) -> str:
    """Use only explicit unit hints on evidence for the selected source."""
    aliases = outcome.get("source_aliases", {}) or {}
    selected_ids = {
        aliases[source.alias] for source in case.sources if source.alias in aliases
    }
    for evidence in outcome.get("evidence", []) or []:
        if not isinstance(evidence, dict) or not selected_ids.intersection(
            evidence.get("source_ids", [])
        ):
            continue
        details = evidence.get("details")
        if isinstance(details, dict):
            units = details.get("units")
            if isinstance(units, dict) and isinstance(units.get(column), str):
                return str(units[column])
    return ""


def score_case(case: EvaluationCase, outcome: dict[str, Any]) -> list[MetricResult]:
    """Score stable known-answer contracts from a public run and its audit evidence."""
    run_state = str(
        outcome.get("run_state", outcome.get("status", "unknown"))
    ).casefold()
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
    clarification = bool(outcome.get("clarification"))
    completed = run_state in {"completed", "complete", "succeeded"}
    awaiting_expected_clarification = (
        run_state in {"awaiting_clarification", "needs_clarification"}
        and clarification
        and case.answerability in {"ambiguous", "unsupported"}
    )
    if completed or awaiting_expected_clarification:
        metrics.append(_result("run_completion", True, run_state=run_state))
    else:
        metrics.append(
            MetricResult(
                name="run_completion",
                status="needs_review",
                details={"run_state": run_state, "clarification": clarification},
            )
        )
    if case.answerability == "unsupported":
        clarified = bool(outcome.get("clarification"))
        metrics.append(
            _result("unsupported_handling", True, clarification_provided=True)
            if clarified
            else MetricResult(
                name="unsupported_handling",
                status="needs_review",
                details={
                    "clarification_provided": False,
                    "reason": "No formal clarification event was recorded; prose is not automatically certified as abstention.",
                },
            )
        )
    elif case.answerability == "ambiguous":
        clarified = bool(outcome.get("clarification"))
        metrics.append(
            _result("ambiguous_handling", True, clarification_provided=True)
            if clarified
            else MetricResult(
                name="ambiguous_handling",
                status="needs_review",
                details={
                    "clarification_provided": False,
                    "reason": "No formal clarification event was recorded; prose is not automatically certified as clarification.",
                },
            )
        )

    for calculation in case.expectations.calculations:
        actual = _observed_calculation(
            case, outcome, calculation.key, calculation.column
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
                and abs(actual_value - calculation.value) <= calculation.tolerance
            )
        except (InvalidOperation, TypeError, ValueError):
            actual_value, matches_value, actual_unit = None, False, ""
        numeric_matches = matches_value
        matches = numeric_matches
        if calculation.unit is not None:
            matches = matches and actual_unit.casefold() == calculation.unit.casefold()
        column_matches = True
        if calculation.column:
            column_matches = (
                str(
                    actual.get("column", "") if isinstance(actual, dict) else ""
                ).casefold()
                == calculation.column.casefold()
            )
            matches = matches and column_matches
        metrics.append(
            _result(
                "calculation:" + calculation.key,
                numeric_matches and column_matches,
                expected_value=str(calculation.value),
                actual_value=str(actual_value) if actual_value is not None else None,
                expected_unit=calculation.unit,
                actual_unit=actual_unit,
            )
        )
        if calculation.unit is not None:
            if not actual_unit:
                metrics.append(
                    MetricResult(
                        name=f"calculation_unit:{calculation.key}",
                        status="needs_review",
                        details={
                            "expected_unit": calculation.unit,
                            "reason": "structured result and selected-source evidence contain no unit hint",
                        },
                    )
                )
            else:
                metrics.append(
                    _result(
                        f"calculation_unit:{calculation.key}",
                        actual_unit.casefold() == calculation.unit.casefold(),
                        expected_unit=calculation.unit,
                        actual_unit=actual_unit,
                    )
                )
            answer = str(
                outcome.get("answer_text", outcome.get("answer", ""))
            ).casefold()
            metrics.append(
                _result(
                    f"answer_unit:{calculation.key}",
                    unit_present(calculation.unit, answer),
                    expected_unit=calculation.unit,
                )
            )

    evidence = outcome.get("evidence", outcome.get("passages", [])) or []
    for index, passage in enumerate(case.expectations.passages):
        found = False
        eligible_citation = False
        for item in evidence:
            if isinstance(item, dict):
                alias = item.get("source_alias") or item.get("alias")
                text = (
                    item.get("text")
                    or item.get("content")
                    or item.get("passage")
                    or item.get("excerpt")
                    or ""
                )
                details_raw = item.get("details")
                details: dict[str, Any] = (
                    details_raw if isinstance(details_raw, dict) else item
                )
                # Audit evidence commonly uses source_id; resolve it through runner's alias map.
                source_ids = item.get("source_ids", []) or [details.get("source_id")]
                if not alias and source_ids:
                    alias = next(
                        (
                            a
                            for a, sid in (
                                outcome.get("source_aliases", {}) or {}
                            ).items()
                            if sid in source_ids
                        ),
                        None,
                    )
                if not text:
                    text = (
                        details.get("text")
                        or details.get("content")
                        or details.get("passage")
                        or details.get("excerpt")
                        or ""
                    )
                if "declared_evidence_ids" in outcome:
                    declared_evidence_ids = set(
                        outcome.get("declared_evidence_ids") or []
                    )
                    if item.get("id") not in declared_evidence_ids:
                        continue
                expected_source = next(
                    (s for s in case.sources if s.alias == passage.source_alias), None
                )
                mapped_source_ids = [
                    sid
                    for source_alias, sid in (
                        outcome.get("source_aliases", {}) or {}
                    ).items()
                    if source_alias == passage.source_alias
                ]
                if mapped_source_ids and not set(mapped_source_ids).intersection(
                    source_ids
                ):
                    continue
                if expected_source and expected_source.version is not None:
                    versions = details.get("source_versions", {})
                    observed_version = next(
                        (versions[sid] for sid in mapped_source_ids if sid in versions),
                        details.get("source_version", details.get("version")),
                    )
                    if str(observed_version) != str(expected_source.version):
                        continue
            else:
                alias, text = getattr(item, "source_alias", None), getattr(
                    item, "text", ""
                )
            if alias == passage.source_alias and _normalise_text(str(text)):
                eligible_citation = True
                if any(
                    _normalise_text(phrase) in _normalise_text(str(text))
                    for phrase in [passage.contains, *passage.contains_any]
                ):
                    found = True
                    break
        status = (
            "pass"
            if found
            else (
                "needs_review"
                if eligible_citation and passage.match_policy == "diagnostic"
                else "fail"
            )
        )
        metrics.append(
            MetricResult(
                name=f"passage:{index + 1}",
                status=status,
                score=None if status == "needs_review" else float(found),
                details={
                    "source_alias": passage.source_alias,
                    "expected_phrase": passage.contains,
                    "accepted_alternatives": passage.contains_any,
                    "match_policy": passage.match_policy,
                    "eligible_citation": eligible_citation,
                    "scope": "Passage anchor presence; factual support requires review",
                },
            )
        )

    artifacts = outcome.get("artifacts", []) or []
    for index, expected_artifact in enumerate(case.expectations.artifacts):
        found = False
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            media_type = artifact.get("media_type") or artifact.get("mime_type")
            schema = artifact.get("schema") or artifact.get("metadata", {})
            exists = artifact.get("exists", artifact.get("downloadable", True))
            declared_ids = set(outcome.get("declared_artifact_ids", []) or [])
            declared = (
                artifact.get("id") in declared_ids
                if "declared_artifact_ids" in outcome
                else True
            )
            if (
                exists
                and declared
                and media_type == expected_artifact.media_type
                and _schema_matches(schema, expected_artifact.schema_)
            ):
                found = True
                break
        metrics.append(
            _result(
                f"artifact:{index + 1}",
                found,
                media_type=expected_artifact.media_type,
                schema=expected_artifact.schema_,
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
        elif source.sha256 is not None:
            metrics.append(
                MetricResult(
                    name=f"source_immutable:{source.alias}",
                    status="needs_review",
                    details={"reason": "source hashes were not present in run audit"},
                )
            )

    has_deterministic_expectations = bool(
        case.expectations.calculations
        or case.expectations.passages
        or case.expectations.artifacts
        or case.expectations.allowed_actions
        or case.answerability != "answerable"
    )
    if not has_deterministic_expectations:
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
    if case.review.requires_human_review or case.expectations.rubric.get(
        "manual_review"
    ):
        metrics.append(
            MetricResult(
                name="manual_review",
                status="needs_review",
                details={
                    "reason": str(
                        case.expectations.rubric.get("manual_review")
                        or "This case requires human review."
                    )[:500]
                },
            )
        )
    return metrics


def _edit_distance(left: list[str], right: list[str]) -> int:
    if len(left) > 20_000 or len(right) > 20_000 or len(left) * len(right) > 4_000_000:
        raise ValueError("edit-distance input exceeds evaluation bounds")
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
    if (
        len(reference) > 20_000
        or len(hypothesis) > 20_000
        or len(reference) * len(hypothesis) > 4_000_000
    ):
        raise ValueError("CER input exceeds evaluation bounds")
    ref = list(reference)
    return (
        _edit_distance(ref, list(hypothesis)) / len(ref)
        if ref
        else (0.0 if not hypothesis else 1.0)
    )


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Whitespace-token WER, retaining native-script tokens without transliteration."""
    ref = reference.split()
    hyp = hypothesis.split()
    if len(ref) > 20_000 or len(hyp) > 20_000 or len(ref) * len(hyp) > 4_000_000:
        raise ValueError("WER input exceeds evaluation bounds")
    return (
        _edit_distance(ref, hyp) / len(ref) if ref else (0.0 if not hypothesis else 1.0)
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
