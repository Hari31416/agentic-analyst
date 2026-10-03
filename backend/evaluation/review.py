"""Bounded human-review template export and import for offline evaluations."""

from __future__ import annotations

import hashlib
from typing import Any

from app.audit.redaction import redact

MAX_REVIEWS = 10_000
MAX_TEXT = 20_000


def _trial_index(report: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    trials = report.get("trials")
    if not isinstance(trials, list) or len(trials) > MAX_REVIEWS:
        raise ValueError("report trials must be a bounded array")
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for trial in trials:
        if not isinstance(trial, dict) or not isinstance(trial.get("case_id"), str):
            raise ValueError("malformed trial")
        repetition = trial.get("repetition")
        if (
            isinstance(repetition, bool)
            or not isinstance(repetition, int)
            or repetition < 0
        ):
            raise ValueError("trial repetition must be a nonnegative integer")
        key = (trial["case_id"], repetition)
        if key in result:
            raise ValueError("duplicate trial identity")
        result[key] = trial
    return result


def _answer_hash(trial: dict[str, Any]) -> str:
    answer = trial.get("answer_text")
    if answer is None:
        answer = ""
    if not isinstance(answer, str):
        raise ValueError("trial answer_text must be a string")
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


def export_review(report: dict[str, Any]) -> dict[str, Any]:
    """Create a JSON-serializable review template tied to exact trial answers."""
    experiment_id = report.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id:
        raise ValueError("report experiment_id is required")
    trials = _trial_index(report)
    cases = {
        str(c.get("case_id")): c for c in report.get("cases", []) if isinstance(c, dict)
    }
    reviews = []
    for (case_id, repetition), trial in sorted(trials.items()):
        case = cases.get(case_id, {})
        reviews.append(
            {
                "case_id": case_id,
                "repetition": repetition,
                "answer_hash": _answer_hash(trial),
                "reviewers": [],
                "provenance": "",
                "rubric": case.get("rubric", {}),
                "rating": None,
                "comment": "",
            }
        )
    return {"schema_version": 1, "experiment_id": experiment_id, "reviews": reviews}


def apply_review(report: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    """Validate imported human labels and attach them without changing auto metrics."""
    if not isinstance(review, dict) or set(review) != {
        "schema_version",
        "experiment_id",
        "reviews",
    }:
        raise ValueError("malformed review document")
    if review.get("schema_version") != 1 or review.get("experiment_id") != report.get(
        "experiment_id"
    ):
        raise ValueError("review experiment does not match report")
    incoming = review.get("reviews")
    if not isinstance(incoming, list) or len(incoming) > MAX_REVIEWS:
        raise ValueError("reviews must be a bounded array")
    trials = _trial_index(report)
    seen: set[tuple[str, int]] = set()
    accepted: dict[tuple[str, int], dict[str, Any]] = {}
    expected_keys = {
        "case_id",
        "repetition",
        "answer_hash",
        "reviewers",
        "provenance",
        "rubric",
        "rating",
        "comment",
    }
    for row in incoming:
        if not isinstance(row, dict) or set(row) != expected_keys:
            raise ValueError("malformed review row")
        case_id, repetition = row.get("case_id"), row.get("repetition")
        if (
            not isinstance(case_id, str)
            or isinstance(repetition, bool)
            or not isinstance(repetition, int)
        ):
            raise ValueError("malformed review identity")
        key = (case_id, repetition)
        if key in seen:
            raise ValueError("duplicate review")
        seen.add(key)
        trial = trials.get(key)
        if trial is None:
            raise ValueError("review references unknown trial")
        if row.get("answer_hash") != _answer_hash(trial):
            raise ValueError("review answer hash does not match trial")
        reviewers = row.get("reviewers")
        provenance = row.get("provenance")
        comment = row.get("comment")
        if (
            reviewers == []
            and provenance == ""
            and row.get("rating") is None
            and comment == ""
        ):
            continue
        if (
            not isinstance(reviewers, list)
            or not reviewers
            or len(reviewers) > 20
            or any(
                not isinstance(x, str) or not x.strip() or len(x) > 200
                for x in reviewers
            )
        ):
            raise ValueError("reviewers must contain bounded names")
        if (
            not isinstance(provenance, str)
            or not provenance.strip()
            or len(provenance) > MAX_TEXT
        ):
            raise ValueError("review provenance is required and bounded")
        if not isinstance(comment, str) or len(comment) > MAX_TEXT:
            raise ValueError("review comment must be bounded text")
        rating = row.get("rating")
        if rating is None or isinstance(rating, (str, int, float, bool)):
            pass
        elif isinstance(rating, dict) and len(rating) <= 100:
            pass
        else:
            raise ValueError("rating must be a scalar or bounded object")
        accepted[key] = redact({k: row[k] for k in expected_keys if k != "answer_hash"})
    result = redact(report)
    if not isinstance(result, dict):
        raise ValueError("report must be an object")
    for trial in result.get("trials", []):
        key = (str(trial.get("case_id")), trial.get("repetition"))
        if key in accepted:
            trial["human_review"] = accepted[key]
            trial["review_status"] = "reviewed"
    return result
