"""Build the offline comparison bundle from retained, exact-answer reviews."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
from evaluation.review import apply_review

RUN_ROOT = ROOT / "evals/runs/real-v2-matrix"
OUT = ROOT / "evals/reviews/real-v2-krutrim-matrix-2026-10-06"
MODEL_ORDER = [
    "gpt-oss-120b",
    "gemma-4-31b-it",
    "gemma-4-26B-A4B-it",
    "Qwen3.6-35B-A3B",
]
OUTPUT_SLUG = {
    "gpt-oss-120b": "gpt-oss-120b",
    "gemma-4-31b-it": "gemma-4-31b-it",
    "gemma-4-26B-A4B-it": "gemma-4-26b-a4b-it",
    "Qwen3.6-35B-A3B": "qwen3-6-35b-a3b",
}


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def file_sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_case_type(case):
    tags = set(case.get("tags", []))
    if "artifact" in tags or "chart" in tags:
        return "artifact generation"
    if "mixed-source" in tags:
        return "mixed source"
    if "survey" in tags:
        return "document retrieval"
    return "retail data analysis"


def telemetry_coverage(matrix):
    """Reuse original retained counters without API calls or report mutation."""
    trials = []
    for item in matrix["models"]:
        path = Path(item["reports"]["json"])
        report = read(RUN_ROOT / path.parent.name / path.name)
        if not report.get("finished_at") or len(report["trials"]) != 50:
            raise ValueError(f"Incomplete run: {item['model']}")
        for trial in report["trials"]:
            if trial["status"] in {"pending", "running"}:
                raise ValueError("Trial is not terminal")
            model = trial.get("telemetry", {}).get("model", {})
            trials.append(
                {
                    "model": item["model"],
                    "case_id": trial["case_id"],
                    "attempts": model.get("attempted_count", trial.get("model_calls"))
                    or 0,
                    "responses": model.get("response_count", 0),
                    "known_response_usage": model.get("usage") or {},
                    "usage_status": model.get("usage_status", "unavailable"),
                }
            )
    coverage = {
        "schema_version": 1,
        "scope": "Retained report response counters; not billing totals",
        "trials": trials,
    }
    write_json(OUT / "telemetry-coverage.json", coverage)
    return coverage


def model_rows():
    matrix = read(RUN_ROOT / "matrix.json")
    by_model = {item["model"]: item for item in matrix["models"]}
    coverage = telemetry_coverage(matrix)
    coverage_by_trial = {
        (row["model"], row["case_id"]): row for row in coverage.get("trials", [])
    }
    cases = {row["id"]: row for row in read(ROOT / "evals/cases/real-v2.json")["cases"]}
    models = []
    flat_cases = []
    for model in MODEL_ORDER:
        item = by_model[model]
        report_rel = Path(item["reports"]["json"])
        slug = report_rel.parent.name
        report_path = RUN_ROOT / slug / report_rel.name
        report = read(report_path)
        checkpoint_path = report_path.parent / "checkpoint.json"
        artifact_manifest_path = report_path.parent / "artifacts/manifest.json"
        output_slug = OUTPUT_SLUG[model]
        review_path = OUT / output_slug / "review.json"
        if not review_path.exists():
            raise FileNotFoundError(f"Missing review for {model}: {review_path}")
        review = read(review_path)
        reviewed_report = apply_review(report, review)
        write_json(OUT / output_slug / "reviewed-report.json", reviewed_report)
        artifact_manifest = report_path.parent / "artifacts/manifest.json"
        artifact_map = {}
        if artifact_manifest.exists():
            manifest = read(artifact_manifest)
            entries = (
                manifest
                if isinstance(manifest, list)
                else manifest.get("artifacts", [])
            )
            for entry in entries:
                if isinstance(entry, dict) and entry.get("id"):
                    artifact_map[entry["id"]] = entry

        review_by_case = {row["case_id"]: row for row in review["reviews"]}
        reviewed_case_rows = []
        for trial in report["trials"]:
            case = cases[trial["case_id"]]
            label = review_by_case.get(trial["case_id"])
            if not label:
                raise ValueError(f"Review missing {model}/{trial['case_id']}")
            expected_hash = hashlib.sha256(
                (trial.get("answer_text") or "").encode("utf-8")
            ).hexdigest()
            if expected_hash != label["answer_hash"]:
                raise ValueError(f"Answer hash mismatch for {model}/{trial['case_id']}")
            rating = label["rating"] or {}
            artifacts = []
            for artifact in trial.get("observations", {}).get("artifacts", []):
                item_art = {
                    key: artifact.get(key)
                    for key in (
                        "id",
                        "display_name",
                        "media_type",
                        "byte_size",
                        "sha256",
                        "durable",
                        "exists",
                    )
                }
                cached = artifact_map.get(artifact.get("id"), {})
                candidate = (
                    cached.get("path")
                    or cached.get("file")
                    or cached.get("relative_path")
                )
                if candidate:
                    item_art["cached_path"] = (
                        f"../../runs/real-v2-matrix/{slug}/artifacts/{candidate}"
                    )
                    item_art["cache_status"] = cached.get("status", "unknown")
                    item_art["cache_sha256_matches_expected"] = cached.get(
                        "expected_sha256"
                    ) == cached.get("download_sha256")
                artifacts.append(item_art)
            tool_calls = []
            for call in trial.get("observations", {}).get("tool_calls", []):
                result = call.get("result") or {}
                error = result.get("error")
                tool_calls.append(
                    {
                        "name": call.get("name"),
                        "decision": call.get("decision"),
                        "status": call.get("status"),
                        "started_at": call.get("started_at"),
                        "finished_at": call.get("finished_at"),
                        "summary": result.get("summary"),
                        "error": (
                            {"code": error.get("code"), "message": error.get("message")}
                            if isinstance(error, dict)
                            else None
                        ),
                        "evidence_ids": result.get("evidence_ids", []),
                        "artifact_ids": result.get("artifact_ids", []),
                    }
                )
            usage = coverage_by_trial.get((model, trial["case_id"]), {})
            record = {
                "model": model,
                "model_slug": slug,
                "experiment_id": report["experiment_id"],
                "run_id": trial.get("run_id"),
                "case_id": case["id"],
                "question": case["question"],
                "language": case.get("language"),
                "task_type": safe_case_type(case),
                "tags": case.get("tags", []),
                "answerability": case.get("answerability"),
                "auto_status": trial.get("status"),
                "run_state": trial.get("run_state"),
                "answer_text": trial.get("answer_text") or "",
                "answer_hash": label["answer_hash"],
                "review": {
                    "overall_task": rating.get("overall_task", "unresolved"),
                    "reasons": rating.get("reasons", []),
                    "comment": label.get("comment", ""),
                    "reviewers": label.get("reviewers", []),
                    "provenance": label.get("provenance", ""),
                },
                "elapsed_seconds": trial.get("elapsed_seconds"),
                "query_seconds": trial.get("query_seconds"),
                "queue_seconds": trial.get("queue_seconds"),
                "ingestion_seconds": trial.get("ingestion_seconds"),
                "model_calls": trial.get("model_calls"),
                "tokens": usage.get("known_response_usage", {}).get("total_tokens"),
                "reported_tokens": trial.get("tokens"),
                "usage_coverage": usage,
                "error": trial.get("error"),
                "metrics": trial.get("metrics", []),
                "evidence_ids": trial.get("evidence_ids", []),
                "artifacts": artifacts,
                "tool_calls": tool_calls,
                "clarification": trial.get("observations", {}).get(
                    "clarification", False
                ),
                "profile_mismatches": trial.get("profile_mismatches", []),
                "audit_limits": trial.get("audit_limits", {}),
                "source_hashes": trial.get("source_hashes", {}),
                "answer_review_ref": f"{output_slug}/review.json",
                "automatic_report_ref": f"../../runs/real-v2-matrix/{slug}/report.json",
                "artifact_review_ref": None,
            }
            reviewed_case_rows.append(record)
            flat_cases.append(record)

        counts = Counter(row["review"]["overall_task"] for row in reviewed_case_rows)
        auto_counts = Counter(row["auto_status"] for row in reviewed_case_rows)
        summary = item["summary"]["overall"]
        model_usage = {}
        for cov in (
            coverage_by_trial[(model, row["case_id"])] for row in reviewed_case_rows
        ):
            for key, value in (cov.get("known_response_usage") or {}).items():
                model_usage[key] = model_usage.get(key, 0) + value
        attempt_count = sum(
            coverage_by_trial[(model, row["case_id"])].get("attempts", 0)
            for row in reviewed_case_rows
        )
        models.append(
            {
                "model": model,
                "slug": slug,
                "output_slug": output_slug,
                "experiment_id": report["experiment_id"],
                "source_report_sha256": file_sha256(report_path),
                "checkpoint_sha256": file_sha256(checkpoint_path),
                "artifact_manifest_sha256": (
                    file_sha256(artifact_manifest_path)
                    if artifact_manifest_path.exists()
                    else None
                ),
                "trials": len(reviewed_case_rows),
                "review_counts": dict(counts),
                "auto_status_counts": dict(auto_counts),
                "median_seconds": summary.get("latency_seconds", {}).get("p50"),
                "p95_seconds": summary.get("latency_seconds", {}).get("p95"),
                "query_seconds": summary.get("query_seconds", {}),
                "queue_seconds": summary.get("queue_seconds", {}),
                "ingestion_seconds": summary.get("ingestion_seconds", {}),
                "model_attempt_count": attempt_count,
                "tool_call_count": summary.get("telemetry", {}).get("tool_call_count"),
                "retained_tool_call_count": summary.get("telemetry", {}).get(
                    "retained_tool_call_count"
                ),
                "model_usage": model_usage,
                "usage_measured_trials": sum(
                    1
                    for row in reviewed_case_rows
                    if coverage_by_trial[(model, row["case_id"])].get("usage_status")
                    == "measured"
                ),
                "usage_unavailable_trials": sum(
                    1
                    for row in reviewed_case_rows
                    if coverage_by_trial[(model, row["case_id"])].get("usage_status")
                    == "unavailable"
                ),
                "usage_coverage_counts": dict(
                    Counter(
                        coverage_by_trial[(model, row["case_id"])].get(
                            "usage_status", "unavailable"
                        )
                        for row in reviewed_case_rows
                    )
                ),
                "automatic_summary": summary,
                "cases": reviewed_case_rows,
            }
        )
    return models, flat_cases


def write_summary(models):
    lines = [
        "# Real-v2 model comparison",
        "",
        "AI-assisted, uncalibrated review of 200 saved trials. Original automatic results remain separate.",
        "",
        "| Model | Complete | Partial | Failed | Complete in added 20 | Original auto failed |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model in models:
        counts = model["review_counts"]
        added = sum(
            "real-v2-added" in c["tags"] and c["review"]["overall_task"] == "complete"
            for c in model["cases"]
        )
        lines.append(
            f"| {model['model']} | {counts.get('complete', 0)}/50 | {counts.get('partial', 0)} | {counts.get('failed', 0)} | {added}/20 | {model['auto_status_counts'].get('failed', 0)} |"
        )
    lines.extend(
        [
            "",
            "The primary agent checked automatic-failure/complete disagreements against saved SQL rows. Three correctly valued answers were kept partial because their retained SQL output missed the requested named single-row contract. Monetary review comments retain decimal precision. Further spot checks clarified wrong GVA growth and an omitted quotation.",
            "",
            "Requested artifact contents remain unverified because the local application API was offline; see README.md and individual case notes. No new model calls or rescoring occurred.",
        ]
    )
    (OUT / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    models, rows = model_rows()
    write_summary(models)
    write_json(
        OUT / "comparison.json",
        {
            "schema_version": 1,
            "title": "Real-v2 model comparison",
            "generated_by": "Primary agent report assembly; Luna / gpt-6-luna answer review",
            "review_provenance": "AI-assisted, uncalibrated, first-pass task-quality review; not human or native-speaker certification.",
            "matrix_path": "../../runs/real-v2-matrix/matrix.json",
            "input_hashes": {
                "matrix.json": file_sha256(RUN_ROOT / "matrix.json"),
                "telemetry-coverage.json": file_sha256(OUT / "telemetry-coverage.json"),
                "real-v2.json": file_sha256(ROOT / "evals/cases/real-v2.json"),
                "real-v2-manifest.json": file_sha256(
                    ROOT / "evals/fixtures/real-v2/manifest.json"
                ),
            },
            "trial_count_per_model": 1,
            "excluded_models": [],
            "limitations": [
                "Each included model has one trial per case; shared source families and no repeatability estimates limit ranking conclusions.",
                "Artifacts are shown from retained metadata and chart schemas; binary/CSV contents were not downloaded for this offline review.",
                "AI review labels are uncalibrated and do not represent human or native-speaker validation.",
                "The automatic status and deterministic metrics remain as originally recorded. AI task-quality labels are separate.",
                "Token totals are known response usage counters retained in the original reports, not provider billing totals. Missing/partial counters are shown explicitly.",
                "Tool-call attempts and durable tool records differ; displayed request counts do not imply every attempt has a retained ToolCall row.",
                "Optional judge calibration, broad ablations, three-repeat release comparisons, and comprehensive quality thresholds are not included.",
            ],
            "models": models,
        },
    )
    with (OUT / "cases.csv").open("w", encoding="utf-8", newline="") as stream:
        fields = [
            "model",
            "experiment_id",
            "run_id",
            "case_id",
            "language",
            "task_type",
            "tags",
            "auto_status",
            "run_state",
            "overall_task",
            "reasons",
            "answer_hash",
            "elapsed_seconds",
            "query_seconds",
            "queue_seconds",
            "ingestion_seconds",
            "model_calls",
            "tokens",
            "error_code",
            "answer_review_ref",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "model": row["model"],
                    "experiment_id": row["experiment_id"],
                    "run_id": row["run_id"],
                    "case_id": row["case_id"],
                    "language": row["language"],
                    "task_type": row["task_type"],
                    "tags": ",".join(row["tags"]),
                    "auto_status": row["auto_status"],
                    "run_state": row["run_state"],
                    "overall_task": row["review"]["overall_task"],
                    "reasons": "; ".join(row["review"]["reasons"]),
                    "answer_hash": row["answer_hash"],
                    "elapsed_seconds": row["elapsed_seconds"],
                    "query_seconds": row["query_seconds"],
                    "queue_seconds": row["queue_seconds"],
                    "ingestion_seconds": row["ingestion_seconds"],
                    "model_calls": row["model_calls"],
                    "tokens": row["tokens"],
                    "error_code": (row["error"] or {}).get("code"),
                    "answer_review_ref": row["answer_review_ref"],
                }
            )
    print(f"Wrote {len(models)} models and {len(rows)} reviewed trial rows to {OUT}")


if __name__ == "__main__":
    main()
