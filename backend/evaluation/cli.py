"""Explicit evaluation entry point; never imported by production routers."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import hashlib
from copy import deepcopy
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
    identity = build_identity(
        cases,
        settings,
        args.api_url,
        args.fixture_root,
        args.repeats,
        args.profile,
        args.timeout,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    # One process owns a checkpoint; concurrency is controlled within that process.
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
        client = ApplicationClient(args.api_url)
        try:
            if not settings.eval_username or not settings.eval_password:
                raise ValueError(
                    "Set EVAL_USERNAME and EVAL_PASSWORD for authenticated evaluation"
                )
            await client.login(
                settings.eval_username, settings.eval_password.get_secret_value()
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
                "scope": "Synthetic deterministic expectations only; human language/grounding review and optional judges are uncalibrated.",
                "minimum_trials_for_release_comparison": 3,
                "trial_count_requirement_met": args.repeats >= 3,
            }
            checkpoint.save()
            return write_reports(report, args.output)
        finally:
            await client.close()


def rescore(report: dict[str, Any], cases: list[EvaluationCase]) -> dict[str, Any]:
    """Revise scoring on retained traces, preserving original execution identity."""
    current = {case.id: case for case in cases}
    original = {case["id"]: case for case in report["identity"]["cases"]}
    revised = deepcopy(report)
    revised["cases"] = [
        {
            "case_id": case.id,
            "question": case.question,
            "language": case.language,
            "tags": case.tags,
            "review": case.review.model_dump(),
            "rubric": case.expectations.rubric,
        }
        for case in cases
    ]
    for trial in revised["trials"]:
        case = current[trial["case_id"]]
        old_case = original[case.id]
        for field in ("question", "language", "sources", "answerability"):
            if old_case.get(field) != case.model_dump(mode="json").get(field):
                raise ValueError(
                    "Rescore cannot change execution inputs; use a fresh experiment"
                )
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
    }
    return revised


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ["list", "run"]:
        child = commands.add_parser(name)
        child.add_argument(
            "--cases", type=Path, default=ROOT / "evals/cases/core-v1.json"
        )
        child.add_argument("--case", action="append", default=[])
        child.add_argument("--tag", action="append", default=[])
        if name == "run":
            child.add_argument("--api-url", default="http://127.0.0.1:8000")
            child.add_argument(
                "--fixture-root", type=Path, default=ROOT / "evals/fixtures"
            )
            child.add_argument("--output", type=Path, required=True)
            child.add_argument("--repeats", type=int, default=1)
            child.add_argument("--concurrency", type=int, default=1)
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
        if args.command in {"list", "run"}:
            cases = load_cases(args.cases, args.case, args.tag)
            if args.command == "list":
                print(
                    json.dumps(
                        [
                            {
                                "id": c.id,
                                "language": c.language,
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
