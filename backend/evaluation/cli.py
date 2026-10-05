"""Explicit evaluation entry point; never imported by production routers."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import hashlib
from copy import copy, deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.config import get_settings
from app.audit.redaction import redact
from pydantic import ValidationError
from evaluation.client import ApplicationClient
from evaluation.contracts import EvaluationCase
from evaluation.identity import ROOT, build_identity, digest
from evaluation.reporting import write_reports
from evaluation.review import apply_review, export_review
from evaluation.runner import (
    Checkpoint,
    run_experiment,
    metric_status,
    score_answer_claims,
)
from evaluation.metrics import score_case

MAX_JSON_BYTES = 32 * 1024 * 1024


def load_json(path: Path) -> Any:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("JSON input exceeds the evaluation cap")
    return json.loads(path.read_text())


def load_cases(path: Path, ids: list[str], tags: list[str]) -> list[EvaluationCase]:
    document = load_json(path)
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != 1
        or not isinstance(document.get("cases"), list)
        or len(document["cases"]) > 500
    ):
        raise ValueError(
            "Case inventory must be schema version 1 with at most 500 cases"
        )
    cases = [EvaluationCase.model_validate(value) for value in document["cases"]]
    identities = {c.id for c in cases}
    if len(identities) != len(cases) or set(ids) - identities:
        raise ValueError("Duplicate or unknown case IDs")
    for case in cases:
        if len({source.alias for source in case.sources}) != len(case.sources):
            raise ValueError("Duplicate source aliases")
    selected = [
        c
        for c in cases
        if (not ids or c.id in ids) and (not tags or set(tags).intersection(c.tags))
    ]
    if not selected:
        raise ValueError("Filters select no cases")
    return selected


async def execute(
    args: argparse.Namespace, cases: list[EvaluationCase]
) -> dict[str, Path]:
    if not args.live:
        raise ValueError(
            "Run requires --live to authorize the configured model/API calls"
        )
    if urlsplit(args.api_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("The first runner requires a loopback application API")
    settings = get_settings()
    eval_username, eval_password = settings.eval_username, settings.eval_password
    if not eval_username or not eval_password:
        raise ValueError(
            "Set EVAL_USERNAME and EVAL_PASSWORD for authenticated evaluation"
        )
    for case in cases:
        requested_language = case.answer_language or case.language
        if requested_language not in settings.supported_languages:
            raise ValueError(
                f"Case {case.id} requests unsupported answer language; set answer_language to a configured canonical language"
            )
    selected_model = getattr(args, "model", None)
    if selected_model:
        if selected_model not in {
            settings.openai_model,
            *settings.openai_allowed_models,
        }:
            raise ValueError("Requested model is not in OPENAI_ALLOWED_MODELS")
        settings = settings.model_copy(update={"openai_model": selected_model})
    if not (
        1 <= args.concurrency <= 4
        and 1 <= args.repeats <= 20
        and 1 <= args.timeout <= 1800
    ):
        raise ValueError(
            "Runner limits: repeats 1..20, concurrency 1..4, timeout 1..1800"
        )
    identity = build_identity(
        cases,
        settings,
        args.api_url,
        args.fixture_root,
        args.repeats,
        args.profile,
        args.timeout,
        args.concurrency,
    )
    client = ApplicationClient(args.api_url)
    try:
        # Authenticate before creating output state, so rejected credentials do
        # not leave a misleading empty checkpoint behind.
        await client.login(eval_username, eval_password.get_secret_value())
        args.output.mkdir(parents=True, exist_ok=True)
        # One process owns a checkpoint; concurrency is controlled within it.
        with (args.output / ".runner.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("Another runner owns this output directory") from error
            checkpoint = Checkpoint(
                args.output / "checkpoint.json",
                identity,
                cases,
                resume=args.resume,
                fresh=args.fresh,
            )
            report = await run_experiment(
                cases,
                checkpoint,
                client,
                args.fixture_root,
                repeats=args.repeats,
                concurrency=args.concurrency,
                timeout=args.timeout,
                profile=args.profile,
                notify=lambda data: print(json.dumps(data), flush=True),
            )
            report["api_base_url"] = args.api_url
            report["gate"] = {
                "passed": all(t["status"] == "passed" for t in report["trials"]),
                "scope": "Deterministic case expectations only; human language, grounding and manual rubrics require review; optional judges are uncalibrated.",
                "minimum_trials_for_release_comparison": 3,
                "trial_count_requirement_met": args.repeats >= 3,
            }
            checkpoint.save()
            return write_reports(report, args.output)
    finally:
        await client.close()


async def execute_matrix(
    args: argparse.Namespace, cases: list[EvaluationCase]
) -> dict[str, Any]:
    """Run each model with a shared concurrency bound and separate checkpoints."""
    models = args.model
    if not models or len(models) > 20 or len(set(models)) != len(models):
        raise ValueError("Choose 1..20 distinct model IDs")
    settings = get_settings()
    if set(models) - {settings.openai_model, *settings.openai_allowed_models}:
        raise ValueError("Requested models are not in OPENAI_ALLOWED_MODELS")
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / ".matrix.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another runner owns this matrix directory") from error
        return await _execute_models(args, cases, models)


async def _execute_models(
    args: argparse.Namespace, cases: list[EvaluationCase], models: list[str]
) -> dict[str, Any]:
    # Stable folders preserve exact IDs without path traversal or case collisions.
    results = []
    for model in models:
        child = copy(args)
        child.model = model
        child.output = args.output / f"model-{digest(model)[:12]}"
        print(json.dumps({"event": "model_started", "model": model}), flush=True)
        paths = await execute(child, cases)
        report = load_json(paths["json"])
        results.append(
            {
                "model": model,
                "experiment_id": report["experiment_id"],
                "reports": {key: str(path) for key, path in paths.items()},
                "summary": report["summary"],
                "gate": report["gate"],
            }
        )
        # Save after each model so an interrupted matrix keeps its completed results.
        summary = {
            "schema_version": 1,
            "models": results,
            "concurrency": args.concurrency,
            "repeats": args.repeats,
            "scope": "Automatic deterministic checks; manual answer review pending.",
        }
        temporary = args.output / "matrix.tmp"
        temporary.write_text(json.dumps(summary, indent=2) + "\n")
        temporary.replace(args.output / "matrix.json")
        print(
            json.dumps(
                {
                    "event": "model_complete",
                    "model": model,
                    "summary": report["summary"]["overall"],
                }
            ),
            flush=True,
        )
    return summary


def rescore(report: dict[str, Any], cases: list[EvaluationCase]) -> dict[str, Any]:
    """Revise scoring on retained traces, preserving original execution identity."""
    current = {case.id: case for case in cases}
    original = {case["id"]: case for case in report["identity"]["cases"]}
    revised = deepcopy(report)
    revised["cases"] = []
    for case in cases:
        old_case = original.get(case.id, {})
        changed_execution_metadata = any(
            old_case.get(field) != case.model_dump(mode="json").get(field)
            for field in (
                "question",
                "language",
                "answer_language",
                "sources",
                "answerability",
            )
        )
        revised["cases"].append(
            {
                "case_id": case.id,
                "question": old_case.get("question", case.question),
                "language": old_case.get("language", case.language),
                "answer_language": old_case.get("answer_language"),
                "tags": old_case.get("tags", case.tags),
                "review": case.review.model_dump(),
                "rubric": case.expectations.rubric,
                **(
                    {
                        "scoring_skipped_reason": "execution metadata retained from original trial"
                    }
                    if changed_execution_metadata
                    else {}
                ),
            }
        )
    for trial in revised["trials"]:
        case = current[trial["case_id"]]
        old_case = original[case.id]
        changed_inputs = [
            field
            for field in (
                "question",
                "language",
                "answer_language",
                "sources",
                "answerability",
            )
            if old_case.get(field) != case.model_dump(mode="json").get(field)
        ]
        if changed_inputs:
            if trial.get("observations"):
                raise ValueError(
                    "Rescore cannot change execution inputs; use a fresh experiment"
                )
            trial["scoring_skipped_reason"] = (
                "execution inputs changed; retained original trial without rescoring"
            )
            continue
        observed = trial.get("observations")
        if (
            not isinstance(observed, dict)
            or observed.get("run_state") not in {"completed", "awaiting_clarification"}
            or trial.get("status")
            in {
                "infrastructure_failure",
                "timeout",
                "model_error",
            }
        ):
            continue
        metrics = score_case(case, observed) + score_answer_claims(
            case, trial.get("answer_text", "")
        )
        trial["previous_scoring_status"] = trial["status"]
        trial["status"] = metric_status(metrics)
        trial["metrics"] = [
            {"code": metric.name, **metric.model_dump(mode="json")}
            for metric in metrics
        ]
    scoring = {
        "cases": [case.model_dump(mode="json") for case in cases],
        "metric_code_hash": digest(
            {
                name: hashlib.sha256(
                    (ROOT / "backend/evaluation" / name).read_bytes()
                ).hexdigest()
                for name in ("metrics.py", "runner.py", "contracts.py", "cli.py")
            }
        ),
    }
    revised["scoring_identity"] = scoring
    revised["execution_experiment_id"] = report.get(
        "execution_experiment_id", report["experiment_id"]
    )
    revised["experiment_id"] = (
        f"{revised['execution_experiment_id']}-score-{digest(scoring)[:8]}"
    )
    revised["gate"] = {
        **report.get("gate", {}),
        "passed": all(trial["status"] == "passed" for trial in revised["trials"]),
        "scope": "Deterministic case expectations only; human language, grounding and manual rubrics require review; optional judges are uncalibrated.",
    }
    return revised


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ["list", "run", "run-matrix"]:
        child = commands.add_parser(name)
        child.add_argument(
            "--cases", type=Path, default=ROOT / "evals/cases/core-v1.json"
        )
        child.add_argument("--case", action="append", default=[])
        child.add_argument("--tag", action="append", default=[])
        if name in {"run", "run-matrix"}:
            child.add_argument("--api-url", default="http://127.0.0.1:8000")
            child.add_argument(
                "--fixture-root", type=Path, default=ROOT / "evals/fixtures"
            )
            child.add_argument("--output", type=Path, required=True)
            child.add_argument("--repeats", type=int, default=1)
            child.add_argument("--concurrency", type=int, default=1)
            if name == "run-matrix":
                child.add_argument("--model", action="append", required=True)
            else:
                child.add_argument("--model", help="Configured allowlisted model ID")
            child.add_argument("--timeout", type=float, default=300)
            child.add_argument(
                "--profile", choices=["basic", "advanced"], default="basic"
            )
            child.add_argument("--live", action="store_true")
            child.add_argument("--resume", action="store_true")
            child.add_argument("--fresh", action="store_true")
            child.add_argument(
                "--strict",
                action="store_true",
                help="Exit 1 when synthetic deterministic gates fail",
            )
    for name in ["render", "review-export", "review-import", "rescore"]:
        child = commands.add_parser(name)
        child.add_argument("report", type=Path)
        child.add_argument("--output", type=Path, required=True)
        if name == "rescore":
            child.add_argument(
                "--cases", type=Path, default=ROOT / "evals/cases/core-v1.json"
            )
        if name == "review-import":
            child.add_argument("review", type=Path)
    args = parser.parse_args()
    try:
        if args.command in {"list", "run", "run-matrix"}:
            cases = load_cases(args.cases, args.case, args.tag)
            if args.command == "list":
                print(
                    json.dumps(
                        [
                            {
                                "id": c.id,
                                "language": c.language,
                                "answer_language": c.answer_language,
                                "tags": c.tags,
                                "review": c.review.provenance,
                            }
                            for c in cases
                        ],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            if args.command == "run-matrix":
                summary = asyncio.run(execute_matrix(args, cases))
                print(json.dumps({"matrix": str(args.output / "matrix.json")}))
                return int(
                    args.strict
                    and any(not row["gate"]["passed"] for row in summary["models"])
                )
            paths = asyncio.run(execute(args, cases))
            print(json.dumps({key: str(value) for key, value in paths.items()}))
            if (
                args.strict
                and not load_json(args.output / "checkpoint.json")["gate"]["passed"]
            ):
                return 1
        else:
            report = load_json(args.report)
            if args.command == "review-export":
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(export_review(report), ensure_ascii=False, indent=2)
                    + "\n"
                )
            else:
                if args.command == "rescore":
                    ids = sorted({trial["case_id"] for trial in report["trials"]})
                    report = rescore(report, load_cases(args.cases, ids, []))
                if args.command == "review-import":
                    report = apply_review(report, load_json(args.review))
                print(
                    json.dumps(
                        {
                            k: str(v)
                            for k, v in write_reports(report, args.output).items()
                        }
                    )
                )
        return 0
    except (ValueError, OSError) as error:
        # Validation messages describe schemas/paths, not credential/provider bodies.
        print(
            json.dumps(
                {
                    "error": type(error).__name__,
                    "message": (
                        "Schema validation failed"
                        if isinstance(error, ValidationError)
                        else redact(str(error))[:300]
                    ),
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
