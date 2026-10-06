import asyncio
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.audit.redaction import redact
from evaluation.cli import load_cases
from evaluation.client import ApplicationClient
from evaluation.client import ApiFailure, _safe_api_error
from evaluation.contracts import EvaluationCase
from evaluation.identity import ROOT, digest, endpoint_identity, fixture_path
from evaluation.runner import Checkpoint, audit_telemetry, run_experiment


def case():
    return EvaluationCase(
        id="unsupported",
        question="Ask for missing source",
        language="en-IN",
        answerability="unsupported",
    )


class Server:
    def __init__(self):
        self.run_posts = 0
        self.run_request = None
        self.audit_config = {}
        self.wait_forever = False
        self.fail_create = False
        self.running_entered = asyncio.Event()

    async def handle(self, request):
        path = request.url.path
        if path == "/api/workspaces":
            if self.fail_create:
                return httpx.Response(503, json={"detail": "private provider body"})
            return httpx.Response(201, json={"id": "workspace"})
        if path.endswith("/threads"):
            return httpx.Response(201, json={"id": "thread"})
        if request.method == "POST" and path.endswith("/runs"):
            self.run_posts += 1
            self.run_request = json.loads(request.content)
            return httpx.Response(
                201, json={"id": json.loads(request.content)["request_id"]}
            )
        if path.endswith("/cancel"):
            self.wait_forever = False
            return httpx.Response(200, json={"state": "cancelled"})
        if path.endswith("/audit/export"):
            return httpx.Response(
                200,
                json={
                    "run": {"config": self.audit_config},
                    "tool_calls": [],
                    "evidence": [],
                    "artifacts": [],
                },
            )
        if path.endswith("/export"):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"assets": []}))
            return httpx.Response(200, content=buffer.getvalue())
        if path.startswith("/api/runs/"):
            self.running_entered.set()
            return httpx.Response(
                200,
                json={
                    "state": (
                        "running" if self.wait_forever else "awaiting_clarification"
                    ),
                    "outcome": {
                        "text": "Please select a source",
                        "clarification": True,
                        "cleanup": "complete",
                    },
                },
            )
        raise AssertionError(f"unexpected endpoint {path}")


async def test_runner_public_api_resume_and_identity_invalidation(tmp_path):
    server = Server()
    client = ApplicationClient(
        "http://localhost", transport=httpx.MockTransport(server.handle)
    )
    identity = {"cases": ["unsupported"], "repeats": 1}
    checkpoint = Checkpoint(tmp_path / "checkpoint.json", identity, [case()])
    result = await run_experiment([case()], checkpoint, client, tmp_path)
    assert result["trials"][0]["status"] == "passed" and server.run_posts == 1
    reused = Checkpoint(tmp_path / "checkpoint.json", identity, [case()], resume=True)
    await run_experiment([case()], reused, client, tmp_path)
    assert server.run_posts == 1
    with pytest.raises(ValueError, match="identity changed"):
        Checkpoint(
            tmp_path / "checkpoint.json",
            {"cases": ["different"]},
            [case()],
            resume=True,
        )
    with pytest.raises(ValueError, match="Checkpoint exists"):
        Checkpoint(tmp_path / "checkpoint.json", identity, [case()])
    await client.close()


async def test_runner_submits_identity_model_and_records_execution_settings(tmp_path):
    server = Server()
    server.audit_config = {"model": "provider/model-a", "retrieval_profile": "basic"}
    client = ApplicationClient(
        "http://localhost", transport=httpx.MockTransport(server.handle)
    )
    checkpoint = Checkpoint(
        tmp_path / "checkpoint.json", {"model": "provider/model-a"}, [case()]
    )
    report = await run_experiment(
        [case()], checkpoint, client, tmp_path, repeats=1, concurrency=3, timeout=90
    )
    assert server.run_request["model"] == "provider/model-a"
    assert report["execution_settings"] == {
        "repeats": 1,
        "concurrency": 3,
        "timeout_seconds": 90,
        "retrieval_profile": "basic",
    }
    assert report["trials"][0]["profile_mismatches"] == []
    assert report["trials"][0]["started_at"] and report["trials"][0]["finished_at"]
    await client.close()


async def test_timeout_and_api_failure_remain_in_checkpoint(tmp_path):
    server = Server()
    server.wait_forever = True
    client = ApplicationClient(
        "http://localhost", transport=httpx.MockTransport(server.handle)
    )
    checkpoint = Checkpoint(tmp_path / "checkpoint.json", {}, [case()])
    report = await run_experiment([case()], checkpoint, client, tmp_path, timeout=1)
    assert (
        report["trials"][0]["status"] == "timeout"
        and report["trials"][0]["cancel_requested"]
    )
    await run_experiment(
        [case()],
        Checkpoint(tmp_path / "checkpoint.json", {}, [case()], resume=True),
        client,
        tmp_path,
    )
    assert server.run_posts == 1
    server.fail_create = True
    failed = Checkpoint(tmp_path / "other.json", {}, [case()])
    result = await run_experiment([case()], failed, client, tmp_path)
    assert result["trials"][0]["status"] == "infrastructure_failure"
    assert "private provider body" not in (tmp_path / "other.json").read_text()
    await client.close()


async def test_interrupted_run_resumes_observation_without_resubmitting(tmp_path):
    server = Server()
    server.wait_forever = True
    client = ApplicationClient(
        "http://localhost", transport=httpx.MockTransport(server.handle)
    )
    checkpoint = Checkpoint(tmp_path / "checkpoint.json", {}, [case()])
    task = asyncio.create_task(run_experiment([case()], checkpoint, client, tmp_path))
    await server.running_entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert checkpoint.report["trials"][0]["run_id"]
    server.wait_forever = False
    resumed = Checkpoint(tmp_path / "checkpoint.json", {}, [case()], resume=True)
    result = await run_experiment([case()], resumed, client, tmp_path)
    assert result["trials"][0]["status"] == "passed" and server.run_posts == 1
    await client.close()


def test_fixture_filters_paths_and_credential_free_identity(tmp_path):
    assert endpoint_identity("http://localhost/v1")["origin"] == "http://localhost"
    with pytest.raises(ValueError):
        endpoint_identity("http://secret:password@localhost/v1")
    with pytest.raises(ValueError):
        fixture_path(tmp_path, "../outside")
    inventory = load_cases(ROOT / "evals/cases/core-v1.json", [], ["repeat-baseline"])
    assert [c.id for c in inventory] == ["data-en", "data-hi"]
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert redact({"total_tokens": 42, "token": "private", "api_key": 123}) == {
        "total_tokens": 42,
        "token": "[redacted]",
        "api_key": "[redacted]",
    }


def test_known_answer_prose_gate_rejects_wrong_numbers_and_units():
    from evaluation.runner import score_answer_claims

    known = load_cases(ROOT / "evals/cases/core-v1.json", ["data-en"], [])[0]
    assert all(
        metric.status == "pass"
        for metric in score_answer_claims(known, "Count 2; INR 25,000.00.")
    )
    assert any(
        metric.status == "fail"
        for metric in score_answer_claims(known, "Count 3; USD 35000.")
    )


def test_unverified_metric_cannot_be_hidden_by_completion_pass():
    from evaluation.runner import metric_status
    from evaluation.contracts import MetricResult

    metrics = [
        MetricResult(name="run_completion", status="pass"),
        MetricResult(name="unit_provenance", status="needs_review"),
    ]
    assert metric_status(metrics) == "needs_review"
    metrics[1] = MetricResult(name="label_provenance", status="needs_review")
    assert metric_status(metrics) == "passed"


def test_devanagari_numeric_literals_are_preserved():
    from evaluation.runner import score_answer_claims

    known = load_cases(ROOT / "evals/cases/core-v1.json", ["data-hi"], [])[0]
    assert all(
        metric.status == "pass"
        for metric in score_answer_claims(known, "आवेदन २, कुल राशि ₹२५०००.००।")
    )


def test_audit_telemetry_extracts_bounded_tool_and_model_trace_without_payloads():
    telemetry = audit_telemetry(
        {
            "run": {
                "outcome": {
                    "model_calls": 3,
                    "tool_calls": 2,
                    "usage": {"total_tokens": "[redacted]"},
                    "error": {
                        "code": "invalid_model_response",
                        "message": "private answer",
                    },
                }
            },
            "tool_calls": [
                {
                    "name": "search_documents",
                    "decision": "allowed",
                    "status": "completed",
                    "started_at": "2026-10-05T10:00:00+00:00",
                    "finished_at": "2026-10-05T10:00:00.125+00:00",
                    "input_reference": {"question": "private query"},
                    "result": {"text": "private result"},
                },
                {
                    "name": "run_sql",
                    "decision": "allowed",
                    "status": "failed",
                    "started_at": "2026-10-05T10:00:01+00:00",
                    "finished_at": "2026-10-05T10:00:02+00:00",
                    "result": {
                        "error": {"code": "database_timeout", "message": "private"}
                    },
                },
            ],
            "events": [
                {
                    "type": "status",
                    "payload": {
                        "message": "Requesting the next action",
                        "model_calls": 1,
                        "prompt": "private",
                    },
                },
                {
                    "type": "model_response_diagnostic",
                    "payload": {
                        "model_calls": 1,
                        "finish_reason": "tool_calls",
                        "duration_ms": 250,
                        "usage": {
                            "prompt_tokens": 80,
                            "completion_tokens": 20,
                            "total_tokens": 100,
                        },
                        "content_characters": 0,
                        "tool_calls": [
                            {"name": "search_documents", "arguments": "private"}
                        ],
                    },
                },
                {
                    "type": "status",
                    "payload": {
                        "message": "Requesting the next action",
                        "model_calls": 2,
                    },
                },
                {
                    "type": "model_request_failed",
                    "payload": {
                        "model_calls": 3,
                        "code": "model_timeout",
                        "retryable": False,
                        "duration_ms": 900,
                    },
                },
                {
                    "type": "status",
                    "payload": {
                        "message": "Requesting the next action",
                        "model_calls": 3,
                        "code": "model_timeout",
                    },
                },
            ],
        }
    )
    assert telemetry["tool_calls"]["count"] == 2
    assert telemetry["tool_calls"]["durations_ms"] == {
        "total": 1125.0,
        "max": 1000.0,
        "measured_count": 2,
    }
    assert telemetry["tool_calls"]["error_counts"] == {"database_timeout": 1}
    assert telemetry["model"]["call_count"] == 3
    assert telemetry["model"]["attempted_count"] == 3
    assert telemetry["model"]["response_count"] == 1
    assert telemetry["model"]["durations_ms"] == {
        "total": 1150.0,
        "max": 900.0,
        "measured_count": 2,
    }
    assert telemetry["model"]["error_counts"] == {
        "model_timeout": 1,
        "invalid_model_response": 1,
    }
    assert telemetry["model"]["usage"] == {
        "prompt_tokens": 80,
        "completion_tokens": 20,
        "total_tokens": 100,
    }
    assert telemetry["model"]["usage_status"] == "partial"
    assert "private" not in json.dumps(telemetry)


def test_checkpoint_retains_numeric_token_counters(tmp_path):
    checkpoint = Checkpoint(tmp_path / "checkpoint.json", {}, [case()])
    trial = checkpoint.trial(case(), 0)
    trial["tokens"] = 31
    trial["telemetry"] = {
        "model": {
            "usage": {"prompt_tokens": 19, "completion_tokens": 12, "total_tokens": 31}
        }
    }
    checkpoint.save()
    saved = json.loads((tmp_path / "checkpoint.json").read_text())
    assert saved["trials"][0]["tokens"] == 31
    assert saved["trials"][0]["telemetry"]["model"]["usage"] == {
        "prompt_tokens": 19,
        "completion_tokens": 12,
        "total_tokens": 31,
    }


def test_rescore_preserves_execution_identity_and_refuses_changed_inputs():
    from evaluation.cli import rescore

    observed = {
        "run_state": "awaiting_clarification",
        "clarification": True,
        "answer_text": "Missing source",
        "tool_calls": [],
        "source_hashes": {},
    }
    original = case()
    report = {
        "experiment_id": "original",
        "identity": {"cases": [original.model_dump(mode="json")]},
        "trials": [
            {
                "case_id": original.id,
                "repetition": 0,
                "status": "failed",
                "answer_text": "Missing source",
                "observations": observed,
            }
        ],
        "gate": {"passed": False},
    }
    scored = rescore(report, [original])
    assert scored["execution_experiment_id"] == "original"
    assert (
        scored["experiment_id"] != "original"
        and scored["trials"][0]["status"] == "passed"
    )
    assert report["trials"][0]["status"] == "failed"
    changed = original.model_copy(update={"question": "Different question"})
    with pytest.raises(ValueError, match="execution inputs"):
        rescore(report, [changed])


@pytest.mark.asyncio
async def test_evaluation_login_uses_bearer_without_retaining_cookie():
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.path == "/api/auth/login":
            assert json.loads(request.content) == {
                "username": "evaluator",
                "password": "test-password",
            }
            return httpx.Response(
                200,
                json={"access_token": "test-token"},
                headers={"Set-Cookie": "analyst_access=test-token; Path=/api"},
            )
        assert request.headers["Authorization"] == "Bearer test-token"
        assert "cookie" not in request.headers
        return httpx.Response(200, json=[])

    client = ApplicationClient("http://test", transport=httpx.MockTransport(handle))
    try:
        await client.login("evaluator", "test-password")
        assert await client.request("GET", "/api/workspaces") == []
        assert len(seen) == 2
    finally:
        await client.close()


def test_safe_422_diagnostics_drop_submitted_values_and_messages():
    payload = json.dumps(
        {
            "detail": [
                {
                    "loc": ["body", "answer_language"],
                    "type": "literal_error",
                    "msg": "private question text",
                    "input": "secret prompt",
                }
            ]
        }
    ).encode()
    safe = _safe_api_error(payload, 422)
    assert safe == {
        "validation_fields": [
            {"field": "body.answer_language", "type": "literal_error"}
        ]
    }
    assert "secret" not in json.dumps(safe)
    assert "private" not in json.dumps(safe)


@pytest.mark.asyncio
async def test_missing_credentials_fail_before_checkpoint_or_output_creation(
    tmp_path, monkeypatch
):
    from evaluation import cli
    from app.config import Settings

    output = tmp_path / "must-not-exist"
    args = SimpleNamespace(
        live=True,
        api_url="http://127.0.0.1:8000",
        fixture_root=tmp_path,
        repeats=1,
        profile="basic",
        timeout=60,
        output=output,
        resume=False,
        fresh=False,
    )
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(_env_file=None, eval_username=None, eval_password=None),
    )
    with pytest.raises(ValueError, match="EVAL_USERNAME and EVAL_PASSWORD"):
        await cli.execute(args, [case()])
    assert not output.exists()


@pytest.mark.asyncio
async def test_source_processing_error_retains_safe_code_and_stage_only():
    async def handle(request):
        return httpx.Response(
            200,
            json=[
                {
                    "id": "doc1",
                    "state": "failed",
                    "stage": "failed",
                    "details": {
                        "error": {
                            "code": "extractor_unavailable",
                            "message": "private document excerpt",
                        }
                    },
                }
            ],
        )

    client = ApplicationClient("http://test", transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(ApiFailure) as failure:
            await client.wait_sources("workspace", [{"document_id": "doc1"}])
        assert failure.value.code == "source_processing_failed"
        assert failure.value.details == {
            "processing_code": "extractor_unavailable",
            "stage": "failed",
            "state": "failed",
        }
        assert "private" not in json.dumps(failure.value.details)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_original_hashes_bound_selected_members_not_all_index_metadata(
    monkeypatch,
):
    import hashlib
    from evaluation import client as module

    original = b"public original" * 10
    metadata = {
        "assets": [
            {"owner_id": "source1", "purpose": "original", "path": "original.pdf"}
        ],
        "metadata": "x" * 700,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(metadata))
        archive.writestr("original.pdf", original)
        archive.writestr("index.json", "x" * 2000)
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 1024)

    async def handle(request):
        return httpx.Response(200, content=buffer.getvalue())

    client = ApplicationClient(
        "http://127.0.0.1", transport=httpx.MockTransport(handle)
    )
    try:
        hashes = await client.original_hashes(
            "workspace1",
            [
                {
                    "id": "source1",
                    "alias": "pdf",
                    "content_hash": hashlib.sha256(original).hexdigest(),
                }
            ],
        )
        assert hashes["pdf"]["before"] == hashes["pdf"]["after"]
        monkeypatch.setattr(module, "MAX_ORIGINAL_BYTES", 100)
        with pytest.raises(ApiFailure, match="archive_original_limit"):
            await client.original_hashes(
                "workspace1", [{"id": "source1", "alias": "pdf"}]
            )
        monkeypatch.setattr(module, "MAX_ARCHIVE_EXPANDED_BYTES", 100)
        with pytest.raises(ApiFailure, match="archive_expansion_limit"):
            await client.original_hashes(
                "workspace1", [{"id": "source1", "alias": "pdf"}]
            )
    finally:
        await client.close()


def test_outcome_usage_does_not_imply_failed_attempt_usage_coverage():
    telemetry = audit_telemetry(
        {
            "run": {"outcome": {"model_calls": 2, "usage": {"total_tokens": 42}}},
            "events": [
                {
                    "type": "status",
                    "payload": {
                        "message": "Requesting the next action",
                        "model_calls": 1,
                    },
                },
                {
                    "type": "model_request_failed",
                    "payload": {"model_calls": 1, "code": "model_provider_error"},
                },
                {
                    "type": "status",
                    "payload": {
                        "message": "Requesting the next action",
                        "model_calls": 2,
                    },
                },
                {
                    "type": "model_response_diagnostic",
                    "payload": {"model_calls": 2, "usage": {"total_tokens": 42}},
                },
            ],
        }
    )
    assert telemetry["model"]["usage"] == {"total_tokens": 42}
    assert telemetry["model"]["usage_measured_calls"] == 1
    assert telemetry["model"]["usage_status"] == "partial"
    assert telemetry["model"]["call_count"] == 2


def test_aggregate_without_response_events_discloses_unavailable_per_call_coverage():
    telemetry = audit_telemetry(
        {"run": {"outcome": {"model_calls": 3, "usage": {"total_tokens": 99}}}}
    )
    assert telemetry["model"]["usage_measured_calls"] == 0
    assert telemetry["model"]["usage_status"] == "reported_aggregate"
    assert telemetry["model"]["usage"]["total_tokens"] == 99


async def test_runner_keeps_partial_response_usage_out_of_aggregate_token_total(
    tmp_path,
):
    server = Server()

    async def handle(request):
        if request.url.path.endswith("/audit/export"):
            return httpx.Response(
                200,
                json={
                    "run": {"config": {}},
                    "tool_calls": [],
                    "evidence": [],
                    "artifacts": [],
                    "events": [
                        {
                            "type": "status",
                            "payload": {
                                "message": "Requesting the next action",
                                "model_calls": 1,
                            },
                        },
                        {
                            "type": "model_response_diagnostic",
                            "payload": {
                                "model_calls": 1,
                                "usage": {"total_tokens": 10},
                            },
                        },
                        {
                            "type": "status",
                            "payload": {
                                "message": "Requesting the next action",
                                "model_calls": 2,
                            },
                        },
                    ],
                },
            )
        return await server.handle(request)

    client = ApplicationClient(
        "http://localhost", transport=httpx.MockTransport(handle)
    )
    try:
        result = await run_experiment(
            [case()],
            Checkpoint(tmp_path / "checkpoint.json", {}, [case()]),
            client,
            tmp_path,
        )
        trial = result["trials"][0]
        assert trial["telemetry"]["model"]["usage"]["total_tokens"] == 10
        assert trial["telemetry"]["model"]["usage_status"] == "partial"
        assert trial["tokens"] is None
    finally:
        await client.close()


def test_numeric_claims_accept_protected_grouping_and_currency_equivalents():
    from decimal import Decimal
    from evaluation.metrics import numeric_claims, unit_present
    from evaluation.runner import score_answer_claims

    known = load_cases(ROOT / "evals/cases/core-v1.json", ["data-hi"], [])[0]
    for separator in ["\u202f", "\u00a0", "\u2009"]:
        answer = f"आवेदन २, कुल राशि ₹२५{separator}०००.००।"
        assert all(m.status == "pass" for m in score_answer_claims(known, answer))
        assert numeric_claims(f"£6{separator}91{separator}364.56") == [
            Decimal("691364.56")
        ]
    assert numeric_claims("22 34") == [Decimal("22"), Decimal("34")]
    assert numeric_claims("691\u202f364.57") == [Decimal("691364.57")]
    assert unit_present("GBP", "£691,364.56")
    assert not unit_present("GBP", "USD 691,364.56")
    assert not unit_present("GBP", "NOTGBP 691,364.56")
    assert not unit_present("GBP", "$691,364.56")
