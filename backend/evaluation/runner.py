"""Isolated public-API trials with durable in-flight state and explicit reuse."""

from __future__ import annotations

import asyncio
import json
import os
import re
from decimal import Decimal, InvalidOperation
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.audit.redaction import redact
from evaluation.client import ApiFailure, ApplicationClient
from evaluation.contracts import EvaluationCase, MetricResult
from evaluation.identity import digest
from evaluation.metrics import score_case


class Checkpoint:
    def __init__(
        self,
        path: Path,
        identity: dict[str, Any],
        cases: list[EvaluationCase],
        *,
        resume: bool = False,
        fresh: bool = False,
    ):
        if resume and fresh:
            raise ValueError("Choose resume or fresh, not both")
        self.path = path
        self.report: dict[str, Any]
        signature = digest(identity)
        if path.exists() and not fresh:
            if not resume:
                raise ValueError(
                    "Checkpoint exists; use --resume or a new directory/--fresh"
                )
            if path.stat().st_size > 32 * 1024 * 1024:
                raise ValueError("Checkpoint exceeds the read cap")
            self.report = json.loads(path.read_text())
            if (
                self.report.get("identity_hash") != signature
                or self.report.get("schema_version") != 1
            ):
                raise ValueError(
                    "Checkpoint identity changed; start an explicit fresh experiment"
                )
        else:
            if resume:
                raise ValueError("No checkpoint exists to resume")
            if path.exists():
                # Preserve the old checkpoint before explicitly creating fresh trials.
                path.rename(path.with_name(f"checkpoint-{uuid4().hex}.json"))
            self.report = {
                "schema_version": 1,
                "experiment_id": f"{signature[:16]}-{uuid4().hex[:8]}",
                "identity_hash": signature,
                "identity": identity,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "cases": [
                    {
                        "case_id": c.id,
                        "language": c.language,
                        "tags": c.tags,
                        "review": c.review.model_dump(),
                        "rubric": c.expectations.rubric,
                    }
                    for c in cases
                ],
                "trials": [],
            }
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as file:
            json.dump(
                redact(self.report), file, ensure_ascii=False, indent=2, default=str
            )
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(self.path)

    def trial(self, case: EvaluationCase, repetition: int) -> dict[str, Any]:
        existing: dict[str, Any] | None = next(
            (
                t
                for t in self.report["trials"]
                if t["case_id"] == case.id and t["repetition"] == repetition
            ),
            None,
        )
        if existing is not None:
            return existing
        trial = {
            "case_id": case.id,
            "language": case.language,
            "repetition": repetition,
            "status": "pending",
            "review_status": case.review.provenance,
            "metrics": [],
            "elapsed_seconds": 0.0,
        }
        self.report["trials"].append(trial)
        self.save()
        return trial


def observations(
    case: EvaluationCase,
    run: dict[str, Any],
    audit: dict[str, Any],
    sources: list[dict[str, Any]],
    hashes: dict[str, Any],
) -> dict[str, Any]:
    outcome = run.get("outcome") or {}
    aliases = {source["alias"]: source["id"] for source in sources}
    evidence = []
    for item in audit.get("evidence", []):
        details = item.get("details") or {}
        for alias, sid in aliases.items():
            if sid in item.get("source_ids", []):
                evidence.append(
                    {
                        **item,
                        "source_alias": alias,
                        "text": details.get("excerpt", details.get("text", "")),
                        "source_version": details.get("source_versions", {}).get(sid),
                    }
                )
    return {
        "run_state": run["state"],
        "answer_text": outcome.get("text", ""),
        "clarification": outcome.get("clarification", False),
        "tool_calls": audit.get("tool_calls", []),
        "evidence": evidence,
        "artifacts": audit.get("artifacts", []),
        "source_hashes": hashes,
        "source_aliases": aliases,
        "declared_evidence_ids": outcome.get("evidence_ids", []),
        "declared_artifact_ids": outcome.get("artifact_ids", []),
    }


def score_answer_claims(case: EvaluationCase, answer: str) -> list[MetricResult]:
    """Check expected numeric claims/units separately from executed result correctness."""
    claims: list[Decimal] = []
    text = re.sub(r"\[(?:evidence|artifact):[^\]]+\]", "", answer)
    for match in re.findall(r"(?<![\w/-])-?\d[\d,]*(?:\.\d+)?(?![\w/-])", text):
        try:
            claims.append(Decimal(match.replace(",", "")))
        except InvalidOperation:
            pass
    results = []
    for calculation in case.expectations.calculations:
        results.append(
            MetricResult(
                name=f"answer_number:{calculation.key}",
                status=(
                    "pass"
                    if any(
                        abs(value - calculation.value) <= calculation.tolerance
                        for value in claims
                    )
                    else "fail"
                ),
                details={
                    "expected": str(calculation.value),
                    "scope": "Numeric literal presence; not semantic proof",
                },
            )
        )
        if calculation.unit:
            present = calculation.unit.casefold() in answer.casefold() or (
                calculation.unit == "INR" and "₹" in answer
            )
            results.append(
                MetricResult(
                    name=f"answer_unit:{calculation.key}",
                    status="pass" if present else "fail",
                    details={"expected": calculation.unit},
                )
            )
    return results


def classify(run: dict[str, Any]) -> str | None:
    if run["state"] in {"completed", "awaiting_clarification"}:
        return None
    error = (run.get("outcome") or {}).get("error") or {}
    code = error.get("code", "run_failed") if isinstance(error, dict) else "run_failed"
    if (
        code.startswith(("model_", "invalid_answer", "invalid_tool"))
        or run["state"] == "budget_exhausted"
    ):
        return "model_error"
    if code.startswith(("sandbox_", "storage_", "worker_", "database_")):
        return "infrastructure_failure"
    return "failed"


def metric_status(metrics: list[MetricResult]) -> str:
    if any(metric.status == "fail" for metric in metrics):
        return "failed"
    if any(
        metric.status == "needs_review" and metric.name != "label_provenance"
        for metric in metrics
    ):
        return "needs_review"
    return (
        "passed"
        if any(metric.status == "pass" for metric in metrics)
        else "needs_review"
    )


async def run_experiment(
    cases: list[EvaluationCase],
    checkpoint: Checkpoint,
    client: ApplicationClient,
    fixture_root: Path,
    *,
    repeats: int = 1,
    concurrency: int = 1,
    timeout: float = 300,
    profile: str = "basic",
    notify: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if not 1 <= repeats <= 20 or not 1 <= concurrency <= 4 or not 1 <= timeout <= 1800:
        raise ValueError(
            "Runner limits: repeats 1..20, concurrency 1..4, timeout 1..1800"
        )
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case: EvaluationCase, repetition: int) -> None:
        async with semaphore:
            trial = checkpoint.trial(case, repetition)
            if trial["status"] not in {"pending", "running"}:
                return  # Completed failures are reused too; resume never hides them.
            started = time.monotonic()
            trial["status"] = "running"
            checkpoint.save()
            try:
                async with asyncio.timeout(timeout):
                    if trial.get("ambiguous_creation"):
                        raise ApiFailure("ambiguous_resource_creation")
                    if not trial.get("workspace_id"):
                        trial["ambiguous_creation"] = True
                        checkpoint.save()
                        workspace = await client.request(
                            "POST",
                            "/api/workspaces",
                            json={
                                "label": f"eval:{checkpoint.report['experiment_id']}:{case.id}:{repetition}"[
                                    :180
                                ]
                            },
                        )
                        trial["workspace_id"] = workspace["id"]
                        trial.pop("ambiguous_creation")
                        checkpoint.save()
                    workspace_id = trial["workspace_id"]
                    ingestion_started = time.monotonic()
                    sources = trial.setdefault("sources", [])
                    for source in case.sources:
                        if any(s["alias"] == source.alias for s in sources):
                            continue
                        trial["ambiguous_creation"] = True
                        checkpoint.save()
                        uploaded = await client.upload(
                            workspace_id, source, fixture_root
                        )
                        expected_hash = (
                            checkpoint.report["identity"]
                            .get("fixtures", {})
                            .get(source.path)
                        )
                        if (
                            expected_hash
                            and uploaded.get("content_hash") != expected_hash
                        ):
                            raise ApiFailure("uploaded_source_hash_mismatch")
                        sources.append(uploaded)
                        trial.pop("ambiguous_creation")
                        checkpoint.save()
                    docs = await client.wait_sources(workspace_id, sources)
                    trial["ingestion_seconds"] = time.monotonic() - ingestion_started
                    trial["document_versions"] = [
                        {
                            k: d.get(k)
                            for k in (
                                "id",
                                "source_version",
                                "extractor_version",
                                "chunker_version",
                                "index_generation_id",
                                "details",
                            )
                        }
                        for d in docs
                    ]
                    if not trial.get("thread_id"):
                        trial["ambiguous_creation"] = True
                        checkpoint.save()
                        thread = await client.request(
                            "POST",
                            f"/api/workspaces/{workspace_id}/threads",
                            json={"label": f"eval:{case.id}"},
                        )
                        trial["thread_id"] = thread["id"]
                        trial.pop("ambiguous_creation")
                        checkpoint.save()
                    if not trial.get("run_id"):
                        # Persist the application idempotency identity before sending.
                        trial["run_id"] = str(uuid4())
                        trial["run_creation_pending"] = True
                        checkpoint.save()
                        await client.request(
                            "POST",
                            f"/api/threads/{trial['thread_id']}/runs",
                            json={
                                "request_id": trial["run_id"],
                                "text": case.question,
                                "answer_language": case.language,
                                "selected_source_ids": [
                                    source["id"] for source in sources
                                ],
                                "selected_dataset_ids": [
                                    d["id"]
                                    for s in sources
                                    for d in s.get("datasets", [])
                                ],
                                "retrieval_profile": profile,
                            },
                        )
                        trial.pop("run_creation_pending")
                        checkpoint.save()
                    run = await client.wait_run(trial["run_id"])
                    audit = await client.request(
                        "GET", f"/api/runs/{trial['run_id']}/audit/export"
                    )
                    trial["actual_run_config"] = audit["run"]["config"]
                    if audit["run"].get("started_at") and audit["run"].get(
                        "finished_at"
                    ):
                        trial["query_seconds"] = (
                            datetime.fromisoformat(audit["run"]["finished_at"])
                            - datetime.fromisoformat(audit["run"]["started_at"])
                        ).total_seconds()
                    expected_profile = checkpoint.report["identity"]
                    actual_profile = trial["actual_run_config"]
                    mismatches = [
                        key
                        for key in (
                            "model",
                            "prompt_version",
                            "policy_version",
                            "retrieval_profile",
                        )
                        if key in expected_profile
                        and actual_profile.get(key) != expected_profile[key]
                    ]
                    trial["profile_mismatches"] = mismatches
                    trial["audit_limits"] = audit.get("limits", {})
                    outcome = run.get("outcome") or {}
                    trial.update(
                        answer_text=outcome.get("text", ""),
                        evidence_ids=outcome.get("evidence_ids", []),
                        artifact_ids=outcome.get("artifact_ids", []),
                        model_calls=outcome.get("model_calls"),
                        tokens=(
                            (outcome.get("usage") or {}).get("total_tokens")
                            if isinstance(
                                (outcome.get("usage") or {}).get("total_tokens"), int
                            )
                            else None
                        ),
                        run_state=run["state"],
                    )
                    hashes = await client.original_hashes(workspace_id, sources)
                    observed = observations(case, run, audit, sources, hashes)
                    # Validate artifact existence over the same public download route.
                    for artifact in observed["artifacts"]:
                        try:
                            payload = await client.bytes(
                                "GET", f"/api/artifacts/{artifact['id']}/content"
                            )
                            artifact["exists"] = True
                            if artifact["media_type"] == "application/json" or artifact[
                                "display_name"
                            ].endswith(".chart.json"):
                                artifact["schema"] = json.loads(payload)
                        except (ApiFailure, ValueError):
                            artifact["exists"] = False
                    metrics = score_case(case, observed) + score_answer_claims(
                        case, trial["answer_text"]
                    )
                    trial["metrics"] = [
                        {"code": m.name, **m.model_dump(mode="json")} for m in metrics
                    ]
                    trial["source_hashes"] = hashes
                    trial["status"] = classify(run) or metric_status(metrics)
                    trial["error"] = redact(outcome.get("error"))
                    if mismatches:
                        trial["status"] = "infrastructure_failure"
                        trial["error"] = {
                            "code": "deployment_profile_mismatch",
                            "fields": mismatches,
                        }
                    # Redacted supporting traces retain tool/evidence IDs and payload bounds.
                    trial["observations"] = redact(observed)
            except TimeoutError:
                trial["status"], trial["error"] = "timeout", {"code": "runner_timeout"}
                if trial.get("run_id"):
                    try:
                        await client.request(
                            "POST", f"/api/runs/{trial['run_id']}/cancel"
                        )
                        trial["cancel_requested"] = True
                    except ApiFailure:
                        trial["cancel_requested"] = False
            except ApiFailure as error:
                trial["status"], trial["error"] = "infrastructure_failure", {
                    "code": error.code,
                    "http_status": error.status,
                }
            except asyncio.CancelledError:
                # Leave the run identity resumable; no execution POST is repeated.
                trial["interrupted"] = True
                checkpoint.save()
                raise
            except Exception as error:
                trial["status"], trial["error"] = "infrastructure_failure", {
                    "code": "runner_error",
                    "type": type(error).__name__,
                }
            finally:
                trial["elapsed_seconds"] = (
                    float(trial.get("elapsed_seconds", 0)) + time.monotonic() - started
                )
                trial["runner_peak_rss_bytes"] = resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
                checkpoint.save()
                if notify:
                    notify(
                        {
                            k: trial.get(k)
                            for k in ("case_id", "repetition", "status", "run_id")
                        }
                    )

    await asyncio.gather(*(one(case, rep) for case in cases for rep in range(repeats)))
    checkpoint.report["finished_at"] = datetime.now(timezone.utc).isoformat()
    checkpoint.save()
    return checkpoint.report
