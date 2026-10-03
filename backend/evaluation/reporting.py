"""Portable, redacted reports for offline evaluation runs."""

from __future__ import annotations

import csv
import html
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import quote

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


def _summary(trials: list[dict[str, Any]]) -> dict[str, Any]:
    latency = [
        float(t["elapsed_seconds"])
        for t in trials
        if isinstance(t.get("elapsed_seconds"), (int, float))
        and t["elapsed_seconds"] >= 0
    ]
    return {
        "count": len(trials),
        "failures": sum(t.get("status") != "passed" for t in trials),
        "latency_seconds": {
            "p50": _percentile(latency, 0.50),
            "p95": _percentile(latency, 0.95),
        },
        "tokens": sum(
            int(t.get("tokens") or 0)
            for t in trials
            if isinstance(t.get("tokens", 0), (int, float))
        ),
        "model_calls": sum(
            int(t.get("model_calls") or 0)
            for t in trials
            if isinstance(t.get("model_calls", 0), (int, float))
        ),
    }


def build_report_view(report: dict[str, Any]) -> dict[str, Any]:
    """Return a redacted report augmented with aggregate EN/HI cohort stats."""
    # The shared redactor treats "token" as sensitive, including this numeric counter.
    token_counts = {
        i: t.get("tokens")
        for i, t in enumerate(report.get("trials", []))
        if isinstance(t, dict)
        and isinstance(t.get("tokens"), (int, float))
        and not isinstance(t.get("tokens"), bool)
    }
    safe = redact(report)
    if not isinstance(safe, dict):
        raise ValueError("report must be an object")
    cases = safe.get("cases", [])
    trials = safe.get("trials", [])
    for index, count in token_counts.items():
        if index < len(trials):
            trials[index]["tokens"] = count
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
    for trial in report.get("trials", []):
        case_id = str(trial.get("case_id", ""))
        case: dict[str, Any] = next(
            (c for c in report.get("cases", []) if str(c.get("case_id")) == case_id), {}
        )
        run_id = str(trial.get("run_id") or "")
        run_link = (
            f'<a href="/api/runs/{quote(run_id, safe="")}/audit">Run audit</a>'
            if run_id
            else ""
        )
        artifact_links = " ".join(
            f'<a href="/api/artifacts/{quote(str(a), safe="")}">{html.escape(str(a))}</a>'
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
            + f"<td>{run_link} {artifact_links}</td><td>{html.escape(str(trial.get('answer_text') or ''))}</td><td>{html.escape(json.dumps(trial.get('metrics', []), ensure_ascii=False))}</td></tr>"
        )
    summary = html.escape(
        json.dumps(report.get("summary", {}), ensure_ascii=False, indent=2)
    )
    table = "".join(rows)
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Evaluation report</title><style>body{{font:15px system-ui;margin:2rem;max-width:1100px}}pre{{white-space:pre-wrap;background:#f4f4f4;padding:1rem}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #bbb;padding:.45rem;text-align:left;vertical-align:top}}th{{background:#eee}}</style><h1>Evaluation report</h1><p>Experiment: {html.escape(str(report.get("experiment_id", "unknown")))}</p><h2>Summary</h2><pre>{summary}</pre><h2>Trials</h2><table><thead><tr><th>Case</th><th>Language</th><th>Question</th><th>Repeat</th><th>Status</th><th>Seconds</th><th>Tokens</th><th>Model calls</th><th>Links</th><th>Answer</th><th>Metrics</th></tr></thead><tbody>{table}</tbody></table></html>"""


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
