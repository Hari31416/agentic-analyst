import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import sessionmaker

from datetime import timedelta

from app.agent.runtime import RunCancelled, RunRuntime
from app.config import Settings
from app.contracts import SafeError, ToolResult
from app.db.models import Base, Event, Job, Message, Run, Thread, ToolCall, Workspace
from app.workers.queue import LeaseLost, claim
from sqlalchemy import func

pytestmark = pytest.mark.integration


@pytest.fixture
def db_factory():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = "test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def queued_run(db_factory, *, config=None):
    with db_factory() as session, session.begin():
        workspace = Workspace(label="runtime test")
        session.add(workspace)
        session.flush()
        thread = Thread(workspace_id=workspace.id, label="runtime test")
        session.add(thread)
        session.flush()
        run = Run(
            thread_id=thread.id,
            state="queued",
            config=config or {"answer_language": "en-IN"},
        )
        session.add(run)
        session.flush()
        run_id = run.id
        session.add(
            Message(
                thread_id=thread.id,
                run_id=run.id,
                role="user",
                content="Calculate 1 + 1",
                selected_source_ids=[],
                references={},
            )
        )
        session.add(
            Job(
                kind="agent_run",
                run_id=run.id,
                dedupe_key=str(uuid4()),
                payload={},
            )
        )
    with db_factory() as session, session.begin():
        task = claim(session, "runtime-test", 60)
    assert task is not None
    return run_id, task


class FakeSandbox:
    def __init__(self, *args, **kwargs):
        self.stopped = []
        self.deleted = []
        self.writes = []
        self.closed = False
        FAKE_SANDBOXES.append(self)

    async def stop(self, session_id):
        self.stopped.append(session_id)

    async def delete_session(self, session_id):
        self.deleted.append(session_id)

    async def write(self, session_id, path, content):
        self.writes.append((session_id, path, content))

    async def aclose(self):
        self.closed = True


FAKE_SANDBOXES: list[FakeSandbox] = []


def test_cancelled_run_cleans_persisted_session_and_resolves_open_tool_call(
    db_factory, monkeypatch
):
    import app.agent.runtime as runtime_module

    FAKE_SANDBOXES.clear()
    session_id = str(uuid4())
    run_id, task = queued_run(
        db_factory,
        config={"answer_language": "en-IN", "sandbox_session_id": session_id},
    )
    with db_factory() as session, session.begin():
        run = session.get(Run, run_id)
        assert run
        run.state = "cancelled"
        tool = ToolCall(
            run_id=run_id,
            provider_call_id="provider-call",
            name="run_python",
            input_reference={},
            decision="allowed",
            status="running",
        )
        session.add(tool)
        session.flush()
        tool_id = tool.id

    monkeypatch.setattr(runtime_module, "SandboxHTTPClient", FakeSandbox)
    settings = Settings(
        _env_file=None,
        sandbox_base_url="http://sandbox.test",
        sandbox_image="sandbox:dev",
    )
    asyncio.run(RunRuntime(task, settings, db_factory).run())

    with db_factory() as session:
        run = session.get(Run, run_id)
        tool = session.get(ToolCall, tool_id)
        assert run and run.outcome["cleanup"] == "complete"
        assert run.state == "cancelled"
        assert tool and tool.status == "failed" and tool.finished_at is not None
        assert FAKE_SANDBOXES[0].stopped == [session_id]
        assert FAKE_SANDBOXES[0].deleted == [session_id]
        assert session.scalar(
            select(Event).where(Event.run_id == run_id, Event.type == "terminal")
        )


def test_unexpected_agent_failure_is_terminal_and_closes_running_tool_call(
    db_factory, monkeypatch
):
    import app.agent.runtime as runtime_module

    run_id, task = queued_run(db_factory)
    with db_factory() as session, session.begin():
        tool = ToolCall(
            run_id=run_id,
            provider_call_id="unfinished-provider-call",
            name="run_python",
            input_reference={},
            decision="allowed",
            status="running",
        )
        session.add(tool)
        session.flush()
        tool_id = tool.id

    class BrokenLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, *_args, **_kwargs):
            raise RuntimeError("sensitive provider details")

    monkeypatch.setattr(runtime_module, "AgentLoop", BrokenLoop)
    settings = Settings(_env_file=None)
    asyncio.run(RunRuntime(task, settings, db_factory).run())

    with db_factory() as session:
        run = session.get(Run, run_id)
        tool = session.get(ToolCall, tool_id)
        assert run and run.state == "failed"
        assert run.outcome["error"]["code"] == "worker_failed"
        assert "sensitive" not in str(run.outcome)
        assert tool and tool.status == "failed" and tool.finished_at is not None
        assert session.scalar(
            select(Event).where(Event.run_id == run_id, Event.type == "terminal")
        )


def test_cancellation_persists_partial_tool_result_and_resolves_tool_call(
    db_factory, monkeypatch
):
    import app.agent.runtime as runtime_module

    run_id, task = queued_run(db_factory)
    with db_factory() as session, session.begin():
        tool = ToolCall(
            run_id=run_id,
            provider_call_id="cancelled-provider-call",
            name="run_python",
            input_reference={},
            decision="allowed",
            status="running",
        )
        session.add(tool)
        session.flush()
        tool_id = tool.id
    started = asyncio.Event()

    class BlockingLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, *_args, **_kwargs):
            started.set()
            await asyncio.Event().wait()

    class InterruptedPython:
        cancelled_result = ToolResult(
            status="partial",
            summary="Partial output was collected before cancellation.",
            error=SafeError(
                code="python_execution_cancelled",
                message="Execution was cancelled.",
            ),
            data={"execution_outcome_unknown": True},
        )

        async def aclose(self):
            pass

    monkeypatch.setattr(runtime_module, "AgentLoop", BlockingLoop)
    runtime = RunRuntime(task, Settings(_env_file=None), db_factory)
    runtime.python = InterruptedPython()
    runtime.active_tool_call_id = tool_id

    async def cancel_run():
        execution = asyncio.create_task(runtime.run())
        await started.wait()
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution

    asyncio.run(cancel_run())
    with db_factory() as session:
        run = session.get(Run, run_id)
        tool = session.get(ToolCall, tool_id)
        assert run and run.state == "failed" and run.outcome["cleanup"] == "complete"
        assert tool and tool.status == "completed"
        assert tool.result["status"] == "partial"
        assert tool.result["data"]["partial_after_cancellation"] is True
        assert tool.finished_at is not None


def test_sandbox_session_is_persisted_before_execute(db_factory, tmp_path, monkeypatch):
    import app.agent.runtime as runtime_module

    from app.agent.runtime import PythonInput
    from app.storage.filesystem import FileStorage

    run_id, task = queued_run(db_factory)
    with db_factory() as session, session.begin():
        tool = ToolCall(
            run_id=run_id,
            provider_call_id="session-tool-call",
            name="run_python",
            input_reference={},
            decision="allowed",
            status="running",
        )
        session.add(tool)
        session.flush()
        tool_id = tool.id

    session_id = str(uuid4())
    storage = FileStorage(tmp_path)

    class PersistBeforeExecute:
        def __init__(self, *_args, **_kwargs):
            self.session = None

        async def _ensure_session(self):
            self.session = SimpleNamespace(id=session_id)
            return self.session

        async def execute(self, *_args, **_kwargs):
            with db_factory() as session:
                run = session.get(Run, run_id)
                assert run and run.config["sandbox_session_id"] == session_id
            return ToolResult(status="ok", summary="execution complete")

    monkeypatch.setattr(runtime_module, "get_storage", lambda _settings: storage)
    monkeypatch.setattr(runtime_module, "PythonExecution", PersistBeforeExecute)
    monkeypatch.setattr(runtime_module, "SandboxHTTPClient", FakeSandbox)
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path,
        sandbox_base_url="http://sandbox.test",
        sandbox_image="sandbox:dev",
    )
    result = asyncio.run(
        RunRuntime(task, settings, db_factory).run_python(
            "workspace",
            run_id,
            tool_id,
            PythonInput(code="print(2)", timeout_seconds=2),
        )
    )
    assert result.status == "ok"


def test_cancellation_during_session_creation_prevents_guest_work(
    db_factory, tmp_path, monkeypatch
):
    import app.agent.runtime as runtime_module

    from app.agent.runtime import PythonInput
    from app.storage.filesystem import FileStorage

    FAKE_SANDBOXES.clear()
    run_id, task = queued_run(db_factory)
    with db_factory() as session, session.begin():
        tool = ToolCall(
            run_id=run_id,
            provider_call_id="cancel-during-create",
            name="run_python",
            input_reference={},
            decision="allowed",
            status="running",
        )
        session.add(tool)
        session.flush()
        tool_id = tool.id

    session_id = str(uuid4())
    storage = FileStorage(tmp_path)
    executed = False

    class CancellationDuringCreate:
        def __init__(self, *_args, **_kwargs):
            pass

        async def _ensure_session(self):
            with db_factory() as session, session.begin():
                run = session.get(Run, run_id)
                assert run
                run.state = "cancelled"
            return SimpleNamespace(id=session_id)

        async def execute(self, *_args, **_kwargs):
            nonlocal executed
            executed = True
            raise AssertionError("cancelled run reached guest execution")

    monkeypatch.setattr(runtime_module, "get_storage", lambda _settings: storage)
    monkeypatch.setattr(runtime_module, "PythonExecution", CancellationDuringCreate)
    monkeypatch.setattr(runtime_module, "SandboxHTTPClient", FakeSandbox)
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path,
        sandbox_base_url="http://sandbox.test",
        sandbox_image="sandbox:dev",
    )
    runtime = RunRuntime(task, settings, db_factory)
    with pytest.raises(RunCancelled):
        asyncio.run(
            runtime.run_python(
                "workspace",
                run_id,
                tool_id,
                PythonInput(
                    code="print(2)",
                    input_artifact_ids=[uuid4()],
                    timeout_seconds=2,
                ),
            )
        )
    with db_factory() as session:
        run = session.get(Run, run_id)
        assert run and run.config["sandbox_session_id"] == session_id
    assert not executed
    assert FAKE_SANDBOXES[0].writes == []


def test_stale_worker_does_not_clean_up_or_terminalize_new_owner_run(db_factory):
    run_id, stale = queued_run(db_factory)
    with db_factory() as session, session.begin():
        session.execute(
            update(Job).where(Job.id == stale.id).values(lease_token=str(uuid4()))
        )
    runtime = RunRuntime(stale, Settings(_env_file=None), db_factory)
    with pytest.raises(LeaseLost):
        asyncio.run(runtime.run())
    with db_factory() as session:
        run = session.get(Run, run_id)
        assert run and run.state == "queued"


@pytest.mark.parametrize(
    ("session_id", "expected_cleanup"),
    [(None, "complete"), ("orphan-session", "failed")],
)
def test_retry_exhaustion_marks_run_terminal(db_factory, session_id, expected_cleanup):
    config = {"answer_language": "en-IN"}
    if session_id:
        config["sandbox_session_id"] = session_id
    run_id, task = queued_run(db_factory, config=config)
    with db_factory() as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.id == task.id)
            .values(
                attempts=3,
                max_attempts=3,
                lease_expires_at=func.now() - timedelta(seconds=1),
            )
        )
    with db_factory() as session, session.begin():
        assert claim(session, "reaper", 60) is None
    with db_factory() as session:
        run = session.get(Run, run_id)
        job = session.get(Job, task.id)
        assert run and run.state == "failed"
        assert run.outcome["error"]["code"] == "retry_limit_exceeded"
        assert run.outcome["cleanup"] == expected_cleanup
        assert "TTL" in run.outcome["cleanup_reason"]
        assert job and job.state == "failed" and job.lease_token is None
