"""Portable, redacted reports for offline evaluation runs."""

from __future__ import annotations

import csv
import html
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from app.audit.redaction import redact

STATUSES = {
    "passed",
    "failed",
    "infrastructure_failure",
    "model_error",
    "timeout",
    "interrupted",
    "needs_review",
}


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def _language(case: dict[str, Any]) -> str:
    raw = str(case.get("language") or case.get("language_tag") or "unknown").lower()
    if raw.startswith("hi") or "hindi" in raw:
        return "Hindi"
    if raw.startswith("en") or "english" in raw:
        return "English"
    return "Other"


def _usage_summary(trials: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [
        t[field]
        for t in trials
        if isinstance(t.get(field), (int, float))
        and not isinstance(t.get(field), bool)
        and t[field] >= 0
    ]
    return {
        "total": sum(values) if values else None,
        "measured_count": len(values),
        "unavailable_count": len(trials) - len(values),
    }


def _summary(trials: list[dict[str, Any]]) -> dict[str, Any]:
    latency = [
        float(t["elapsed_seconds"])
        for t in trials
        if isinstance(t.get("elapsed_seconds"), (int, float))
        and t["elapsed_seconds"] >= 0
    ]
    status_counts: dict[str, int] = {}
    metric_counts = {"pass": 0, "fail": 0, "not_applicable": 0, "needs_review": 0}
    for trial in trials:
        status = str(trial.get("status", "unknown"))
        status_counts[status] = status_counts.get(status, 0) + 1
        for metric in trial.get("metrics", []):
            if isinstance(metric, dict):
                metric_status = metric.get("status")
                if metric_status in metric_counts:
                    metric_counts[metric_status] += 1
                else:
                    metric_counts["unknown"] = metric_counts.get("unknown", 0) + 1
    return {
        "count": len(trials),
        "status_counts": status_counts,
        "known_answer_failure_trials": sum(
            any(
                isinstance(m, dict) and m.get("status") == "fail"
                for m in t.get("metrics", [])
            )
            for t in trials
        ),
        "deterministic_metric_counts": metric_counts,
        "latency_seconds": {
            "p50": _percentile(latency, 0.50),
            "p95": _percentile(latency, 0.95),
        },
        "tokens": _usage_summary(trials, "tokens"),
        "model_calls": _usage_summary(trials, "model_calls"),
    }


def build_report_view(report: dict[str, Any]) -> dict[str, Any]:
    """Return a redacted report augmented with aggregate EN/HI cohort stats."""
    # The shared redactor treats "token" as sensitive, including this numeric counter.
    preserved_usage = {
        (i, field): t.get(field)
        for i, t in enumerate(report.get("trials", []))
        if isinstance(t, dict)
        for field in ("tokens", "model_calls")
        if isinstance(t.get(field), (int, float)) and not isinstance(t.get(field), bool)
    }
    safe = redact(report)
    if not isinstance(safe, dict):
        raise ValueError("report must be an object")
    cases = safe.get("cases", [])
    trials = safe.get("trials", [])
    for (index, field), count in preserved_usage.items():
        if index < len(trials):
            trials[index][field] = count
    if not isinstance(cases, list) or not isinstance(trials, list):
        raise ValueError("cases and trials must be arrays")
    case_languages = {
        str(c.get("case_id")): _language(c) for c in cases if isinstance(c, dict)
    }
    cohorts: dict[str, list[dict[str, Any]]] = {"English": [], "Hindi": []}
    for trial in trials:
        if isinstance(trial, dict):
            lang = case_languages.get(str(trial.get("case_id")), "Other")
            if lang in cohorts:
                cohorts[lang].append(trial)
    safe["summary"] = {
        "overall": _summary([t for t in trials if isinstance(t, dict)]),
        "cohorts": {k: _summary(v) for k, v in cohorts.items()},
    }
    return safe


def _safe_csv(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value)
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _viewer(report: dict[str, Any]) -> str:
    rows = []
    identity_value = report.get("identity")
    identity: dict[str, Any] = (
        identity_value if isinstance(identity_value, dict) else {}
    )
    api_origin = (
        report.get("api_base_url")
        or identity.get("api_base_url")
        or identity.get("origin")
    )
    parsed_origin = urlsplit(str(api_origin)) if api_origin else None
    if (
        parsed_origin
        and parsed_origin.scheme in {"http", "https"}
        and parsed_origin.netloc
        and not parsed_origin.username
        and not parsed_origin.password
    ):
        api_origin = f"{parsed_origin.scheme}://{parsed_origin.netloc}{parsed_origin.path.rstrip('/')}"
    else:
        api_origin = ""
    for trial in report.get("trials", []):
        case_id = str(trial.get("case_id", ""))
        case: dict[str, Any] = next(
            (c for c in report.get("cases", []) if str(c.get("case_id")) == case_id), {}
        )
        case_review = case.get("review")
        if not isinstance(case_review, dict):
            case_review = {}
        run_id = str(trial.get("run_id") or "")
        run_link = (
            f'<a href="{html.escape(api_origin, quote=True)}/api/runs/{quote(run_id, safe="")}/audit">Run audit</a>'
            if run_id
            else ""
        )
        artifact_links = " ".join(
            f'<a href="{html.escape(api_origin, quote=True)}/api/artifacts/{quote(str(a), safe="")}">{html.escape(str(a))}</a>'
            for a in trial.get("artifact_ids", [])
            if isinstance(a, (str, int))
        )
        rows.append(
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(x))}</td>"
                for x in (
                    case_id,
                    case.get("language", ""),
                    case.get("question", ""),
                    trial.get("repetition", ""),
                    trial.get("status", ""),
                    trial.get("elapsed_seconds", ""),
                    trial.get("tokens", ""),
                    trial.get("model_calls", ""),
                )
            )
            + f"<td>{run_link} {artifact_links}</td><td>{html.escape(str(trial.get('answer_text') or ''))}</td><td>{html.escape(json.dumps(trial.get('metrics', []), ensure_ascii=False))}</td><td>{html.escape(str(trial.get('review_status') or case_review.get('provenance', 'unreviewed')))}</td></tr>"
        )
    summary = html.escape(
        json.dumps(report.get("summary", {}), ensure_ascii=False, indent=2)
    )
    table = "".join(rows)
    calibration_note = "Deterministic metric results do not establish human review or judge calibration. Review provenance is shown per trial; absent review and judge scores remain unavailable."
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Evaluation report</title><style>body{{font:15px system-ui;margin:2rem;max-width:1100px}}pre{{white-space:pre-wrap;background:#f4f4f4;padding:1rem}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #bbb;padding:.45rem;text-align:left;vertical-align:top}}th{{background:#eee}}</style><h1>Evaluation report</h1><p>Experiment: {html.escape(str(report.get("experiment_id", "unknown")))}</p><h2>Summary</h2><p>{html.escape(calibration_note)}</p><pre>{summary}</pre><h2>Trials</h2><table><thead><tr><th>Case</th><th>Language</th><th>Question</th><th>Repeat</th><th>Status</th><th>Seconds</th><th>Tokens</th><th>Model calls</th><th>Links</th><th>Answer</th><th>Metrics</th><th>Review provenance</th></tr></thead><tbody>{table}</tbody></table></html>"""


def write_reports(report: dict[str, Any], out_dir: Path) -> dict[str, Path]:
    """Write redacted JSON, spreadsheet-safe CSV, and a static HTML viewer."""
    view = build_report_view(report)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, csv_path, html_path = (
        out_dir / "report.json",
        out_dir / "trials.csv",
        out_dir / "index.html",
    )
    json_path.write_text(
        json.dumps(view, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    columns = [
        "case_id",
        "repetition",
        "status",
        "run_id",
        "thread_id",
        "workspace_id",
        "elapsed_seconds",
        "model_calls",
        "tokens",
        "metrics",
        "error",
        "answer_text",
        "evidence_ids",
        "artifact_ids",
        "review_status",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for trial in view.get("trials", []):
            row = {k: _safe_csv(trial.get(k)) for k in columns}
            if trial.get("status") not in STATUSES:
                row["status"] = _safe_csv(trial.get("status"))
            writer.writerow(row)
    html_path.write_text(_viewer(view), encoding="utf-8")
    return {"json": json_path, "csv": csv_path, "html": html_path}
