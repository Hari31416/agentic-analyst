import asyncio
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest

from app.audit.redaction import redact
from evaluation.cli import load_cases
from evaluation.client import ApplicationClient
from evaluation.contracts import EvaluationCase
from evaluation.identity import ROOT, digest, endpoint_identity, fixture_path
from evaluation.runner import Checkpoint, run_experiment


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
                    "run": {"config": {}},
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
