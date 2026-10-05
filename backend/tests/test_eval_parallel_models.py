import asyncio
import json
from argparse import Namespace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.db.models import Run

# Reuse the SQLite API fixture to exercise the actual public request contract.
from tests.test_chat_api import client_db


def test_model_allowlist_snapshot_and_idempotency(client_db, monkeypatch):
    import app.api.chat as chat

    client, sessions, (_, thread), _ = client_db
    original = chat.get_settings()
    monkeypatch.setattr(
        chat,
        "get_settings",
        lambda: original.model_copy(update={"openai_allowed_models": ["candidate"]}),
    )
    payload = {"text": "compare", "request_id": str(uuid4()), "model": "candidate"}
    response = client.post(f"/api/threads/{thread}/runs", json=payload)
    assert response.status_code == 201
    with sessions() as session:
        assert session.get(Run, response.json()["id"]).config["model"] == "candidate"
    assert client.post(f"/api/threads/{thread}/runs", json=payload).status_code == 201
    payload["model"] = "test"
    assert client.post(f"/api/threads/{thread}/runs", json=payload).status_code == 409
    payload["model"] = "unconfigured"
    assert client.post(f"/api/threads/{thread}/runs", json=payload).status_code == 422


@pytest.mark.parametrize("count", [0, 5])
def test_worker_capacity_bound(count):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, worker_concurrency=count)


async def test_matrix_is_sequential_and_model_folders_are_distinct(
    tmp_path, monkeypatch
):
    import evaluation.cli as cli

    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(openai_model="a", openai_allowed_models=["A/../b"]),
    )
    seen = []
    active = 0

    async def execute(args, cases):
        nonlocal active
        active += 1
        assert active == 1
        seen.append((args.model, args.output, args.concurrency))
        await asyncio.sleep(0)
        args.output.mkdir(parents=True)
        path = args.output / "report.json"
        path.write_text(
            json.dumps(
                {
                    "experiment_id": args.model,
                    "summary": {"overall": {}},
                    "gate": {"passed": False},
                }
            )
        )
        active -= 1
        return {"json": path}

    monkeypatch.setattr(cli, "execute", execute)
    result = await cli.execute_matrix(
        Namespace(model=["a", "A/../b"], output=tmp_path, concurrency=4, repeats=1), []
    )
    assert [row[0] for row in seen] == ["a", "A/../b"]
    assert all(row[1].parent == tmp_path and row[2] == 4 for row in seen)
    assert len({row[1] for row in seen}) == 2
    assert len(result["models"]) == 2
    assert json.loads((tmp_path / "matrix.json").read_text()) == result


async def test_pool_stops_siblings_when_child_exits(monkeypatch):
    import app.workers.main as worker

    monkeypatch.setattr(
        worker, "get_settings", lambda: SimpleNamespace(worker_concurrency=4)
    )
    children = []

    class Child:
        returncode = None

        def __init__(self, first):
            self.first = first
            self.done = asyncio.Event()
            self.terminated = False

        async def wait(self):
            if self.first:
                self.returncode = 1
                return 1
            await self.done.wait()
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = 0
            self.done.set()

    async def spawn(*args, **kwargs):
        assert kwargs["env"]["WORKER_CONCURRENCY"] == "1"
        child = Child(not children)
        children.append(child)
        return child

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="worker process exited"):
        await worker.run_worker_pool()
    assert len(children) == 4
    assert all(c.terminated for c in children[1:])


async def test_runner_concurrency_bound_is_real(tmp_path):
    from evaluation.contracts import EvaluationCase
    from evaluation.runner import Checkpoint, run_experiment
    from tests.test_evaluation_runner import Server
    from evaluation.client import ApplicationClient
    import httpx

    server = Server()
    active = peak = 0
    release = asyncio.Event()

    async def handle(request):
        nonlocal active, peak
        if request.url.path.startswith("/api/runs/") and not request.url.path.endswith(
            "/export"
        ):
            active += 1
            peak = max(peak, active)
            if active == 3:
                release.set()
            await asyncio.wait_for(release.wait(), timeout=2)
            await asyncio.sleep(0)
            active -= 1
        return await server.handle(request)

    cases = [
        EvaluationCase(
            id=f"case-{i}",
            question="Ask",
            language="en-IN",
            answerability="unsupported",
        )
        for i in range(6)
    ]
    client = ApplicationClient(
        "http://localhost", transport=httpx.MockTransport(handle)
    )
    try:
        report = await run_experiment(
            cases,
            Checkpoint(tmp_path / "checkpoint.json", {}, cases),
            client,
            tmp_path,
            concurrency=3,
        )
        assert peak == 3
        assert len(report["trials"]) == server.run_posts == 6
        assert all(t["status"] == "passed" for t in report["trials"])
    finally:
        await client.close()
