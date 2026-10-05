"""Validate promotion and retained roles through the worker transaction."""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.agent.runtime import RunRuntime
from app.config import Settings
from app.contracts import FinalAnswer
from app.db.models import Artifact, Event, Message, Run
from tests.integration.test_runtime import db_factory, queued_run

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("deliverable", [True, False])
def test_worker_promotes_only_final_deliverables(db_factory, monkeypatch, deliverable):
    run_id, task = queued_run(db_factory)
    identity = str(uuid4())
    with db_factory() as session, session.begin():
        session.add(
            Artifact(
                id=identity,
                run_id=run_id,
                role="intermediate",
                storage_key=f"derived/{run_id}/result.csv",
                display_name="result.csv",
                media_type="text/csv",
                sha256="0" * 64,
                byte_size=4,
                durable=True,
            )
        )

    class FinishedLoop:
        usage = {}
        model_calls = 1
        calls = 1

        def __init__(self, *args, **kwargs):
            pass

        async def run(self, *args, **kwargs):
            return FinalAnswer(
                text="Partial retained result" if deliverable else "Supporting result",
                artifact_ids=[UUID(identity)],
                output_artifact_ids=[UUID(identity)] if deliverable else [],
            )

    monkeypatch.setattr("app.agent.runtime.AgentLoop", FinishedLoop)
    asyncio.run(RunRuntime(task, Settings(_env_file=None), db_factory).run())
    with db_factory() as session:
        assert session.get(Run, run_id).state == "completed"
        artifact = session.get(Artifact, identity)
        assert artifact.role == ("output" if deliverable else "intermediate")
        answer = session.scalar(
            select(Message).where(Message.run_id == run_id, Message.role == "assistant")
        )
        assert answer.references["output_artifact_ids"] == (
            [identity] if deliverable else []
        )
        event = session.scalar(
            select(Event).where(Event.run_id == run_id, Event.type == "answer")
        )
        assert event.payload["output_artifact_ids"] == (
            [identity] if deliverable else []
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["run_python", "run_sql"])
async def test_tools_assign_roles_without_model_classification(
    db_factory, tmp_path, monkeypatch, tool_name
):
    import json
    from types import SimpleNamespace
    from app.agent.protocol import ModelToolCall
    from app.agent.runtime import PythonInput
    from app.contracts import ArtifactInfo, ToolResult
    from app.db.models import Dataset, Source, Thread
    from app.sources.files import profile_upload
    from app.storage.filesystem import FileStorage
    from app.tools.structured import SQLInput

    run_id, task = queued_run(db_factory)
    storage = FileStorage(tmp_path)
    content = b"name,value\nA,2\n"
    stored = storage.put(f"originals/{run_id}/data.csv", content)
    profile = profile_upload("data.csv", content)[0]
    with db_factory() as session, session.begin():
        run = session.get(Run, run_id)
        thread = session.get(Thread, run.thread_id)
        source = Source(
            workspace_id=thread.workspace_id,
            kind="csv",
            state="ready",
            display_name="data.csv",
            storage_key=stored.key,
            content_hash=stored.sha256,
        )
        session.add(source)
        session.flush()
        dataset = Dataset(
            source_id=source.id,
            source_version=1,
            identity="data",
            schema_version="profile-v1",
            details=profile["details"],
        )
        session.add(dataset)
        session.flush()
        dataset_id = dataset.id
        run.selected_source_ids = [source.id]
        run.config = {
            **run.config,
            "source_versions": {source.id: 1},
            "selected_dataset_ids": [dataset.id],
        }

    class FakeGuest:
        async def _ensure_session(self):
            return SimpleNamespace(id="fake-session")

        async def execute(self, code, tool_id, output_paths, **kwargs):
            descriptors = []
            for name in output_paths:
                body = (
                    json.dumps(
                        {
                            "columns": ["name", "value"],
                            "rows": [{"name": "A", "value": 2}],
                            "row_count": 1,
                            "truncated": False,
                        }
                    ).encode()
                    if name.endswith(".json")
                    else content
                )
                saved = storage.put(f"derived/{run_id}/{tool_id}/{name}", body)
                descriptor = ArtifactInfo(
                    id=uuid4(),
                    storage_key=saved.key,
                    media_type=(
                        "application/json" if name.endswith(".json") else "text/csv"
                    ),
                    byte_size=saved.byte_size,
                    sha256=saved.sha256,
                    run_id=run_id,
                    tool_call_id=tool_id,
                ).model_dump(mode="json")
                descriptors.append({**descriptor, "display_name": name})
            return ToolResult(
                status="ok",
                summary="Retained result",
                artifact_ids=[UUID(row["id"]) for row in descriptors],
                data={"artifacts": descriptors},
            )

    class FakeSandbox:
        async def write(self, *args):
            pass

    monkeypatch.setattr("app.agent.runtime.get_storage", lambda _: storage)
    monkeypatch.setattr("app.tools.structured.get_storage", lambda _: storage)
    runtime = RunRuntime(
        task,
        Settings(_env_file=None, sandbox_base_url="http://fake", sandbox_image="fake"),
        db_factory,
    )
    runtime.python = FakeGuest()
    runtime.sandbox = FakeSandbox()
    arguments = (
        PythonInput(
            code="print(2)", input_dataset_ids=[dataset_id], output_paths=["result.csv"]
        )
        if tool_name == "run_python"
        else SQLInput(
            sql=f"SELECT * FROM data_{UUID(dataset_id).hex}", dataset_ids=[dataset_id]
        )
    )
    result = await runtime.dispatch(
        tool_name,
        ModelToolCall(
            id="roles-call", name=tool_name, arguments=arguments.model_dump_json()
        ),
        arguments,
    )
    assert result.status == "ok", result.model_dump()
    with db_factory() as session:
        roles = {
            row.display_name: row.role
            for row in session.scalars(
                select(Artifact).where(Artifact.run_id == run_id)
            )
        }
    assert roles["code.py"] == "execution_code"
    assert roles[f"input-{dataset_id}.csv"] == "input_snapshot"
    assert roles["result.csv"] == "intermediate"
    if tool_name == "run_sql":
        assert roles["query.sql"] == "execution_code"
        assert roles["query-result.json"] == "metadata"
