"""Isolated public-API trials with durable in-flight state and explicit reuse."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter
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
                        "question": c.question,
                        "language": c.language,
                        "answer_language": c.answer_language,
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
        trial: dict[str, Any] = {
            "case_id": case.id,
            "language": case.language,
            "answer_language": case.answer_language,
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


def _elapsed_ms(started: Any, finished: Any) -> float | None:
    """Return bounded, non-negative elapsed milliseconds for exported timestamps."""
    if not isinstance(started, str) or not isinstance(finished, str):
        return None
    try:
        start = datetime.fromisoformat(started.replace("Z", "+00:00"))
        finish = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        elapsed = (finish - start).total_seconds() * 1000
    except (ValueError, TypeError):
        return None
    return round(elapsed, 3) if 0 <= elapsed <= 86_400_000 else None


def _safe_count(value: Any) -> int | None:
    return (
        value
        if isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= 10**12
        else None
    )


def _safe_duration(value: Any) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= 86_400_000
    ):
        return round(float(value), 3)
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def audit_telemetry(audit: dict[str, Any]) -> dict[str, Any]:
    """Reduce the public audit export to bounded operational telemetry.

    Event and tool payload text is deliberately excluded: answers, prompts,
    arguments, and private reasoning do not belong in evaluation telemetry.
    """
    raw_tools = audit.get("tool_calls", [])
    tool_rows = raw_tools if isinstance(raw_tools, list) else []
    sequence: list[dict[str, Any]] = []
    tool_statuses: Counter[str] = Counter()
    tool_errors: Counter[str] = Counter()
    tool_durations = []
    for row in tool_rows[:500]:
        if not isinstance(row, dict):
            continue
        result = _as_dict(row.get("result"))
        error = _as_dict(result.get("error"))
        status = str(row.get("status") or "unknown")[:40]
        tool_statuses[status] += 1
        error_code = error.get("code")
        if isinstance(error_code, str) and error_code:
            tool_errors[error_code[:80]] += 1
        duration = _elapsed_ms(row.get("started_at"), row.get("finished_at"))
        if duration is not None:
            tool_durations.append(duration)
        sequence.append(
            {
                "sequence": len(sequence) + 1,
                "name": str(row.get("name") or "unknown")[:80],
                "decision": str(row.get("decision") or "unknown")[:40],
                "status": status,
                "error_code": error_code[:80] if isinstance(error_code, str) else None,
                "duration_ms": duration,
            }
        )

    raw_events = audit.get("events", [])
    event_rows = raw_events if isinstance(raw_events, list) else []
    attempts: set[int] = set()
    responses: set[int] = set()
    model_errors: Counter[str] = Counter()
    model_sequence: list[dict[str, Any]] = []
    model_durations: list[float] = []
    model_usage: Counter[str] = Counter()
    usage_calls = 0
    for row in event_rows[:500]:
        if not isinstance(row, dict):
            continue
        event_type = row.get("type")
        payload = _as_dict(row.get("payload"))
        call_number = _safe_count(payload.get("model_calls"))
        if (
            event_type == "status"
            and payload.get("message") == "Requesting the next action"
            and call_number
        ):
            attempts.add(call_number)
            model_sequence.append({"call": call_number, "status": "requested"})
        elif event_type == "model_response_diagnostic" and call_number:
            responses.add(call_number)
            duration = _safe_duration(payload.get("duration_ms"))
            if duration is not None:
                model_durations.append(duration)
            response_usage = _as_dict(payload.get("usage"))
            clean_usage = {
                key: value
                for key in (
                    "prompt_tokens",
                    "completion_tokens",
                    "total_tokens",
                    "input_tokens",
                    "output_tokens",
                )
                if (value := _safe_count(response_usage.get(key))) is not None
            }
            if clean_usage:
                usage_calls += 1
                model_usage.update(clean_usage)
            descriptor = {
                "call": call_number,
                "status": "response",
                "finish_reason": str(payload.get("finish_reason") or "unknown")[:40],
                "duration_ms": duration,
                "usage": clean_usage or None,
                "content_characters": _safe_count(payload.get("content_characters")),
                "tool_names": (
                    [
                        str(tool.get("name") or "unknown")[:80]
                        for tool in payload.get("tool_calls", [])[:20]
                        if isinstance(tool, dict)
                    ]
                    if isinstance(payload.get("tool_calls"), list)
                    else []
                ),
            }
            model_sequence.append(descriptor)
        elif event_type == "model_request_failed" and call_number:
            code = payload.get("code")
            if isinstance(code, str) and code:
                model_errors[code[:80]] += 1
            duration = _safe_duration(payload.get("duration_ms"))
            if duration is not None:
                model_durations.append(duration)
            model_sequence.append(
                {
                    "call": call_number,
                    "status": "failed",
                    "error_code": code[:80] if isinstance(code, str) else None,
                    "retryable": (
                        payload.get("retryable")
                        if isinstance(payload.get("retryable"), bool)
                        else None
                    ),
                    "duration_ms": duration,
                }
            )

    run = _as_dict(audit.get("run"))
    outcome = _as_dict(run.get("outcome"))
    reported_model_calls = _safe_count(outcome.get("model_calls"))
    reported_tool_calls = _safe_count(outcome.get("tool_calls"))
    model_count = max([*attempts, *responses, reported_model_calls or 0], default=0)
    if isinstance(outcome.get("error"), dict):
        code = outcome["error"].get("code")
        if isinstance(code, str) and code and code not in model_errors:
            model_errors[code[:80]] += 1

    # Accept usage only from the adapter's explicitly allowlisted numeric
    # response diagnostics, or numeric totals exposed by the public outcome.
    raw_usage = _as_dict(outcome.get("usage"))
    outcome_usage = {
        key: value
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if (value := _safe_count(raw_usage.get(key))) is not None
    }
    usage = outcome_usage or dict(model_usage)
    if outcome_usage:
        usage_calls = model_count
    return {
        "tool_calls": {
            "count": (
                reported_tool_calls
                if reported_tool_calls is not None
                else len(sequence)
            ),
            "retained_count": len(sequence),
            "status_counts": dict(tool_statuses),
            "error_counts": dict(tool_errors),
            "durations_ms": {
                "total": round(sum(tool_durations), 3),
                "max": round(max(tool_durations), 3) if tool_durations else None,
                "measured_count": len(tool_durations),
            },
            "sequence": sequence,
        },
        "model": {
            "call_count": model_count,
            "attempted_count": len(attempts),
            "response_count": len(responses),
            "error_counts": dict(model_errors),
            "sequence": model_sequence[:500],
            "durations_ms": {
                "total": round(sum(model_durations), 3),
                "max": round(max(model_durations), 3) if model_durations else None,
                "measured_count": len(model_durations),
            },
            "usage": usage or None,
            "usage_measured_calls": usage_calls,
            "usage_status": (
                "measured"
                if usage_calls == model_count and usage_calls
                else "partial" if usage_calls else "unavailable_in_public_audit"
            ),
        },
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
    checkpoint.report["execution_settings"] = {
        "repeats": repeats,
        "concurrency": concurrency,
        "timeout_seconds": timeout,
        "retrieval_profile": profile,
    }
    checkpoint.report.setdefault("started_at", datetime.now(timezone.utc).isoformat())
    checkpoint.save()

    async def one(case: EvaluationCase, repetition: int) -> None:
        async with semaphore:
            trial: dict[str, Any] = checkpoint.trial(case, repetition)
            if trial["status"] not in {"pending", "running"}:
                return  # Completed failures are reused too; resume never hides them.
            started = time.monotonic()
            trial.setdefault("started_at", datetime.now(timezone.utc).isoformat())
            trial["status"] = "running"
            checkpoint.save()

            def progress(stage: str, status: str, **safe_fields: Any) -> None:
                if not notify:
                    return
                notify(
                    {
                        "event": "progress",
                        "case_id": case.id[:120],
                        "repetition": repetition,
                        "stage": stage[:40],
                        "status": status[:40],
                        **safe_fields,
                    }
                )

            try:
                async with asyncio.timeout(timeout):
                    progress("trial", "started")
                    if trial.get("ambiguous_creation"):
                        raise ApiFailure("ambiguous_resource_creation")
                    if not trial.get("workspace_id"):
                        progress("workspace", "creating")
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
                        progress("workspace", "ready")
                    workspace_id = trial["workspace_id"]
                    ingestion_started = time.monotonic()
                    sources = trial.setdefault("sources", [])
                    for source in case.sources:
                        if any(s["alias"] == source.alias for s in sources):
                            continue
                        progress("upload", "started", source_alias=source.alias[:80])
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
                        progress("upload", "complete", source_alias=source.alias[:80])
                    progress("ingestion", "waiting")
                    docs = await client.wait_sources(
                        workspace_id,
                        sources,
                        heartbeat=lambda: progress("ingestion", "heartbeat"),
                    )
                    progress("ingestion", "ready")
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
                        progress("thread", "creating")
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
                        progress("thread", "ready")
                    if not trial.get("run_id"):
                        # Persist the application idempotency identity before sending.
                        trial["run_id"] = str(uuid4())
                        trial["run_creation_pending"] = True
                        checkpoint.save()
                        progress("run_submission", "submitting")
                        await client.request(
                            "POST",
                            f"/api/threads/{trial['thread_id']}/runs",
                            json={
                                "request_id": trial["run_id"],
                                "text": case.question,
                                "answer_language": case.answer_language
                                or case.language,
                                "selected_source_ids": [
                                    source["id"] for source in sources
                                ],
                                "selected_dataset_ids": [
                                    d["id"]
                                    for s in sources
                                    for d in s.get("datasets", [])
                                ],
                                "retrieval_profile": profile,
                                **(
                                    {"model": checkpoint.report["identity"]["model"]}
                                    if isinstance(
                                        checkpoint.report.get("identity"), dict
                                    )
                                    and checkpoint.report["identity"].get("model")
                                    else {}
                                ),
                            },
                        )
                        trial.pop("run_creation_pending")
                        checkpoint.save()
                    progress("run_submission", "submitted", run_id=trial["run_id"])
                    progress("run", "waiting")
                    run = await client.wait_run(
                        trial["run_id"],
                        heartbeat=lambda: progress("run", "heartbeat"),
                    )
                    progress(
                        "run",
                        "complete",
                        run_state=str(run.get("state", "unknown"))[:40],
                    )
                    progress("audit", "loading")
                    audit = await client.request(
                        "GET", f"/api/runs/{trial['run_id']}/audit/export"
                    )
                    trial["actual_run_config"] = audit["run"]["config"]
                    progress("audit", "complete")
                    run_started = audit["run"].get("started_at")
                    run_finished = audit["run"].get("finished_at")
                    query_ms = _elapsed_ms(run_started, run_finished)
                    queue_ms = _elapsed_ms(audit["run"].get("created_at"), run_started)
                    trial["query_seconds"] = (
                        query_ms / 1000 if query_ms is not None else None
                    )
                    trial["queue_seconds"] = (
                        queue_ms / 1000 if queue_ms is not None else None
                    )
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
                    trial["telemetry"] = audit_telemetry(audit)
                    outcome = run.get("outcome") or {}
                    outcome_usage = _as_dict(outcome.get("usage"))
                    trial.update(
                        answer_text=outcome.get("text", ""),
                        evidence_ids=outcome.get("evidence_ids", []),
                        artifact_ids=outcome.get("artifact_ids", []),
                        model_calls=(
                            _safe_count(outcome.get("model_calls"))
                            if _safe_count(outcome.get("model_calls")) is not None
                            else trial["telemetry"]["model"]["call_count"]
                        ),
                        tokens=(
                            _safe_count(outcome_usage.get("total_tokens"))
                            if _safe_count(outcome_usage.get("total_tokens"))
                            is not None
                            else None
                        ),
                        run_state=run["state"],
                    )
                    if trial["tokens"] is None:
                        trial["tokens"] = (
                            trial["telemetry"]["model"]["usage"].get("total_tokens")
                            if trial["telemetry"]["model"]["usage"]
                            else None
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
                    progress("scoring", trial["status"])
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
                    **error.details,
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
                trial["finished_at"] = datetime.now(timezone.utc).isoformat()
                trial["runner_peak_rss_bytes"] = resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
                checkpoint.save()
                if notify:
                    progress("trial", trial["status"], run_id=trial.get("run_id"))

    await asyncio.gather(*(one(case, rep) for case in cases for rep in range(repeats)))
    checkpoint.report["finished_at"] = datetime.now(timezone.utc).isoformat()
    checkpoint.save()
    return checkpoint.report
