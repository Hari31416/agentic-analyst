from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.chat as chat
from app.config import Settings
from app.db.models import Artifact, Base, Job, Message, Run, Source, Thread, Workspace
from app.db.repository import append_event
from app.db.session import get_session
from app.evidence.validation import validate_answer
from app.contracts import FinalAnswer
from app.main import app
from app.storage.filesystem import FileStorage


@pytest.fixture
def client_db(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def dependency():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    settings = Settings(
        _env_file=None,
        openai_base_url="http://test/v1",
        openai_api_key="test",
        openai_model="test",
        storage_root=tmp_path,
    )
    monkeypatch.setattr(chat, "get_settings", lambda: settings)
    monkeypatch.setattr(chat, "get_storage", lambda: FileStorage(tmp_path))
    monkeypatch.setattr(chat, "factory", lambda: sessions)
    with sessions() as session, session.begin():
        workspace = Workspace(label="one")
        session.add(workspace)
        session.flush()
        thread = Thread(workspace_id=workspace.id, label="one")
        session.add(thread)
        session.flush()
        ids = workspace.id, thread.id
    try:
        yield TestClient(app), sessions, ids, tmp_path
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_request_id_prevents_duplicate_dispatch(client_db):
    client, sessions, (_, thread_id), _ = client_db
    payload = {"text": "calculate", "request_id": str(uuid4()), "answer_language": "hi"}
    first = client.post(f"/api/threads/{thread_id}/runs", json=payload)
    second = client.post(f"/api/threads/{thread_id}/runs", json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["answer_language"] == "hi-IN"
    with sessions() as session:
        assert len(session.scalars(select(Job)).all()) == 1
        assert len(session.scalars(select(Message)).all()) == 1
    payload["text"] = "different input"
    assert (
        client.post(f"/api/threads/{thread_id}/runs", json=payload).status_code == 409
    )


def test_selected_source_workspace_and_single_active_run(client_db):
    client, sessions, (_, thread_id), _ = client_db
    with sessions() as session, session.begin():
        other = Workspace(label="two")
        session.add(other)
        session.flush()
        source = Source(workspace_id=other.id, kind="csv", display_name="other.csv")
        session.add(source)
        session.flush()
        source_id = source.id
    assert (
        client.post(
            f"/api/threads/{thread_id}/runs",
            json={"text": "bad", "selected_source_ids": [source_id]},
        ).status_code
        == 422
    )
    assert (
        client.post(f"/api/threads/{thread_id}/runs", json={"text": "one"}).status_code
        == 201
    )
    assert (
        client.post(f"/api/threads/{thread_id}/runs", json={"text": "two"}).status_code
        == 409
    )


def test_queued_cancel_and_event_replay(client_db):
    client, sessions, (_, thread_id), _ = client_db
    run_id = client.post(
        f"/api/threads/{thread_id}/runs", json={"text": "cancel"}
    ).json()["id"]
    cancelled = client.post(f"/api/runs/{run_id}/cancel").json()
    assert cancelled["state"] == "cancelled"
    assert cancelled["outcome"]["cleanup"] == "complete"
    full = client.get(f"/api/runs/{run_id}/events").text
    assert "id: 1" in full and "id: 2" in full
    replay = client.get(
        f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "1"}
    ).text
    assert "id: 1" not in replay and "id: 2" in replay
    assert (
        client.get(
            f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "bad"}
        ).status_code
        == 422
    )
    with sessions() as session:
        assert session.scalar(select(Job)).state == "cancelled"


def test_download_preview_and_reference_scope(client_db):
    client, sessions, (_, thread_id), root = client_db
    with sessions() as session, session.begin():
        run = Run(thread_id=thread_id, state="completed")
        session.add(run)
        session.flush()
        item = FileStorage(root).put(
            f"derived/{run.id}/result.csv", b"total\n25000.00\n"
        )
        artifact = Artifact(
            run_id=run.id,
            storage_key=item.key,
            display_name="result.csv",
            media_type="text/csv",
            byte_size=item.byte_size,
            sha256=item.sha256,
        )
        session.add(artifact)
        session.flush()
        artifact_id = artifact.id
        hostile_file = FileStorage(root).put(
            f"derived/{run.id}/hostile.csv", b"label\n=1+1\n-12.5\n"
        )
        hostile_artifact = Artifact(
            run_id=run.id,
            storage_key=hostile_file.key,
            display_name="hostile.csv",
            media_type="text/csv",
            byte_size=hostile_file.byte_size,
            sha256=hostile_file.sha256,
        )
        session.add(hostile_artifact)
        session.flush()
        hostile_id = hostile_artifact.id
        validate_answer(
            session, run, FinalAnswer(text="total", artifact_ids=[artifact_id])
        )
        with pytest.raises(ValueError):
            validate_answer(
                session, run, FinalAnswer(text="bad", artifact_ids=[uuid4()])
            )
        workspace = Workspace(label="other")
        session.add(workspace)
        session.flush()
        other_thread = Thread(workspace_id=workspace.id, label="other")
        session.add(other_thread)
        session.flush()
        other_run = Run(thread_id=other_thread.id)
        session.add(other_run)
        session.flush()
        with pytest.raises(ValueError, match="another thread"):
            validate_answer(
                session, other_run, FinalAnswer(text="bad", artifact_ids=[artifact_id])
            )
    response = client.get(f"/api/artifacts/{artifact_id}/content")
    assert response.content == b"total\n25000.00\n"
    assert "result.csv" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-export-sanitized"] == "spreadsheet-formulas"
    assert response.headers["x-artifact-sha256"] == artifact.sha256
    import hashlib

    assert (
        response.headers["x-export-sha256"]
        == hashlib.sha256(response.content).hexdigest()
    )
    assert response.headers["x-export-sha256"] == artifact.sha256
    hostile_response = client.get(f"/api/artifacts/{hostile_id}/content")
    assert hostile_response.content == b"label\r\n'=1+1\r\n-12.5\r\n"
    assert hostile_response.headers["x-export-sha256"] != hostile_artifact.sha256
    preview = client.get(
        f"/api/artifacts/{artifact_id}/preview?max_characters=5"
    ).json()
    assert preview["text"] == "total" and preview["truncated"] is True


def test_retrieval_profile_is_persisted_and_part_of_idempotency(client_db):
    client, sessions, (_, thread_id), _ = client_db
    payload = {
        "text": "overview",
        "request_id": str(uuid4()),
        "retrieval_profile": "advanced",
    }
    first = client.post(f"/api/threads/{thread_id}/runs", json=payload)
    assert first.status_code == 201
    assert first.json()["retrieval_profile"] == "advanced"
    with sessions() as session:
        run = session.get(Run, first.json()["id"])
        assert run.config["retrieval_settings"]["candidate_budget"] == 60
    payload["retrieval_profile"] = "basic"
    assert (
        client.post(f"/api/threads/{thread_id}/runs", json=payload).status_code == 409
    )
    payload["retrieval_profile"] = "imaginary"
    assert (
        client.post(f"/api/threads/{thread_id}/runs", json=payload).status_code == 422
    )


def test_retrieval_traces_remain_inspectable_without_citations(client_db):
    from app.db.models import ToolCall

    client, sessions, (_, thread_id), _ = client_db
    run_id = client.post(
        f"/api/threads/{thread_id}/runs", json={"text": "missing document"}
    ).json()["id"]
    with sessions() as session, session.begin():
        session.add(
            ToolCall(
                run_id=run_id,
                provider_call_id="empty",
                decision="allowed",
                name="search_documents",
                status="completed",
                result={
                    "status": "partial",
                    "data": {
                        "trace": [{"stage": "rerank", "status": "unavailable"}],
                        "passages": [],
                    },
                },
            )
        )
    view = client.get(f"/api/runs/{run_id}/retrieval").json()
    assert view["profile"] == "basic"
    assert view["tools"][0]["trace"][0]["status"] == "unavailable"


@pytest.mark.parametrize("kind", ["tool_rejected", "answer_rejected"])
async def test_answer_rejection_diagnostics_are_durable(
    client_db, monkeypatch, caplog, kind
):
    import logging
    from app.agent.runtime import RunRuntime
    from app.db.models import AuditEvent, Event, ToolCall
    import app.audit.redaction as redaction

    _, sessions, (_, thread_id), _ = client_db
    with sessions() as session, session.begin():
        run = Run(thread_id=thread_id, state="running")
        session.add(run)
        session.flush()
        run_id = run.id
    monkeypatch.setattr(
        redaction, "configured_secrets", lambda: ("private-diagnostic-secret",)
    )
    runtime = RunRuntime(None, Settings(_env_file=None), sessions)
    monkeypatch.setattr(runtime, "guard", lambda session: session.get(Run, run_id))
    payload = {
        "code": "invalid_answer",
        "name": "finish_answer" if kind == "tool_rejected" else "content_answer",
        "validation_errors": [
            {
                "field": "answer",
                "type": "reference_validation",
                "message": "unknown artifact private-diagnostic-secret",
            }
        ],
    }
    if kind == "tool_rejected":
        payload["call_id"] = "provider-call"
    with caplog.at_level(logging.WARNING):
        await runtime.event(kind, payload)
    with sessions() as session:
        event = session.scalar(select(Event).where(Event.run_id == run_id))
        audit = session.scalar(select(AuditEvent).where(AuditEvent.run_id == run_id))
        assert event.payload["validation_errors"] == audit.details["validation_errors"]
        assert (
            event.payload["validation_errors"][0]["message"]
            == "unknown artifact [redacted]"
        )
        if kind == "tool_rejected":
            tool = session.scalar(select(ToolCall).where(ToolCall.run_id == run_id))
            assert (
                tool.input_reference["validation_errors"]
                == event.payload["validation_errors"]
            )
        else:
            assert not session.scalars(
                select(ToolCall).where(ToolCall.run_id == run_id)
            ).all()
    assert "unknown artifact" in caplog.text
    assert "private-diagnostic-secret" not in caplog.text
    assert caplog.records[-1].run_id == run_id


async def test_policy_rejection_does_not_duplicate_provider_call(
    client_db, monkeypatch
):
    from app.agent.runtime import RunRuntime
    from app.agent.protocol import ModelToolCall
    from app.db.models import ToolCall, Event
    from app.tools.structured import SourceInput

    _, sessions, (_, thread_id), _ = client_db
    with sessions() as session, session.begin():
        run = Run(
            thread_id=thread_id, state="running", selected_source_ids=[], config={}
        )
        session.add(run)
        session.flush()
        run_id = run.id
    runtime = RunRuntime(None, Settings(_env_file=None), sessions)
    monkeypatch.setattr(runtime, "guard", lambda session: session.get(Run, run_id))
    arguments = SourceInput(source_id=uuid4())
    call = ModelToolCall(
        id="denied-call", name="inspect_schema", arguments=arguments.model_dump_json()
    )
    result = await runtime.dispatch(call.name, call, arguments)
    assert result.status == "rejected"
    assert result.error.code == "source_not_selected"
    await runtime.event(
        "tool_rejected",
        {
            "name": call.name,
            "call_id": call.id,
            "code": result.error.code,
            "validation_errors": [],
        },
    )
    with sessions() as session:
        tools = session.scalars(select(ToolCall).where(ToolCall.run_id == run_id)).all()
        assert len(tools) == 1
        assert tools[0].result["error"]["code"] == "source_not_selected"
        assert (
            tools[0].input_reference["policy"]["reason_code"] == "source_not_selected"
        )
        assert (
            session.scalar(select(Event).where(Event.run_id == run_id)).type
            == "tool_rejected"
        )


@pytest.mark.parametrize(
    "failure, expected_state, expected_code",
    [
        ("budget", "budget_exhausted", "budget_exhausted"),
        ("model", "failed", "model_invalid_response"),
        ("value", "failed", "invalid_model_response"),
        ("unexpected", "failed", "worker_failed"),
    ],
)
async def test_runtime_preserves_failure_when_logging(
    client_db, monkeypatch, caplog, failure, expected_state, expected_code
):
    import app.agent.runtime as runtime_module
    from app.agent.loop import BudgetExhausted
    from app.agent.model import ModelError
    from app.db.models import Event, ToolCall
    from app.workers.queue import Claim

    client, sessions, (_, thread_id), _ = client_db
    result = client.post(
        f"/api/threads/{thread_id}/runs",
        json={"text": "calculate", "request_id": str(uuid4())},
    )
    assert result.status_code == 201
    run_id = result.json()["id"]
    with sessions() as session, session.begin():
        tool = ToolCall(
            run_id=run_id,
            provider_call_id="pending-call",
            name="run_sql",
            input_reference={},
            decision="allowed",
            status="running",
        )
        session.add(tool)
        session.flush()
        tool_id = tool.id

    exceptions = {
        "budget": BudgetExhausted("Tool call limit reached"),
        "model": ModelError("model_invalid_response", "Invalid provider response"),
        "value": ValueError("invalid answer"),
        "unexpected": RuntimeError("private failure details"),
    }

    class BrokenLoop:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, *_args, **_kwargs):
            raise exceptions[failure]

    monkeypatch.setattr(runtime_module, "AgentLoop", BrokenLoop)
    task = Claim(str(uuid4()), "agent_run", str(uuid4()), {}, run_id, 1)
    runtime = runtime_module.RunRuntime(task, Settings(_env_file=None), sessions)
    monkeypatch.setattr(
        runtime,
        "guard",
        lambda session, allow_cancelled=False: session.get(Run, run_id),
    )
    await runtime.run()
    with sessions() as session:
        run = session.get(Run, run_id)
        assert run.state == expected_state
        assert run.outcome["error"]["code"] == expected_code
        assert run.outcome["cleanup"] == "complete"
        assert "private failure" not in str(run.outcome)
        tool = session.get(ToolCall, tool_id)
        assert tool.status == "failed"
        assert tool.result["error"]["code"] == expected_code
        assert session.scalar(
            select(Event).where(Event.run_id == run_id, Event.type == "terminal")
        )
    assert any(record.run_id == run_id for record in caplog.records)


async def test_runtime_retains_short_citation_mapping_across_runs(
    client_db, monkeypatch
):
    import json
    import app.agent.runtime as runtime_module
    from app.agent.protocol import ModelResponse, ModelToolCall
    from app.contracts import ToolResult
    from app.db.models import Evidence
    from app.workers.queue import Claim

    client, sessions, (_, thread_id), _ = client_db
    evidence_id = str(uuid4())
    prior_refs = None
    for turn in range(2):
        created = client.post(
            f"/api/threads/{thread_id}/runs", json={"text": "cite result"}
        )
        assert created.status_code == 201
        run_id = created.json()["id"]
        if turn == 0:
            with sessions() as session, session.begin():
                session.add(
                    Evidence(
                        id=evidence_id,
                        run_id=run_id,
                        kind="document",
                        source_ids=[],
                        details={"excerpt": "Saved passage", "location": {"page": 4}},
                    )
                )
        responses = iter(
            [
                ModelResponse(
                    finish_reason="tool_calls",
                    tool_calls=[
                        ModelToolCall(
                            id=f"search-{turn}",
                            name="search_documents",
                            arguments='{"query":"result"}',
                        )
                    ],
                ),
                ModelResponse(
                    finish_reason="tool_calls",
                    tool_calls=[
                        ModelToolCall(
                            id=f"finish-{turn}",
                            name="finish_answer",
                            arguments=json.dumps(
                                {"text": "Result [e1]", "evidence_ids": ["e1"]}
                            ),
                        )
                    ],
                ),
            ]
        )

        class Model:
            async def complete(self, messages, schemas):
                if turn:
                    context = json.dumps(messages)
                    assert "Result [e1]" in context
                    assert evidence_id not in context
                return next(responses)

        monkeypatch.setattr(
            runtime_module, "OpenAICompatibleModel", lambda settings: Model()
        )
        runtime = runtime_module.RunRuntime(
            Claim(str(uuid4()), "agent_run", str(uuid4()), {}, run_id, 1),
            Settings(_env_file=None),
            sessions,
        )
        monkeypatch.setattr(
            runtime,
            "guard",
            lambda session, allow_cancelled=False: session.get(Run, run_id),
        )

        async def dispatch(name, call, args):
            return ToolResult(
                status="ok",
                summary="Retrieved saved passage",
                evidence_ids=[evidence_id],
                data={
                    "passages": [
                        {"evidence_id": evidence_id, "excerpt": "Saved passage"}
                    ]
                },
            )

        monkeypatch.setattr(runtime, "dispatch", dispatch)
        await runtime.run()
        with sessions() as session:
            run = session.get(Run, run_id)
            assert run.state == "completed", run.outcome
            assert run.config["reference_aliases"]["e1"] == evidence_id
            message = session.scalar(
                select(Message).where(
                    Message.run_id == run_id, Message.role == "assistant"
                )
            )
            assert message.content == f"Result [evidence:{evidence_id}]"
            assert message.references["evidence_ids"] == [evidence_id]
            assert message.references["reference_aliases"]["e1"] == evidence_id
            if prior_refs:
                assert message.references["reference_aliases"] == prior_refs
            prior_refs = message.references["reference_aliases"]


def test_short_citations_require_declaration_and_current_provenance(client_db):
    import json
    from app.agent.references import ModelReferences
    from app.db.models import Evidence

    _, sessions, (_, thread_id), _ = client_db
    source_id, evidence_id, run_id = [str(uuid4()) for _ in range(3)]
    refs = ModelReferences({"e1": evidence_id})
    with sessions() as session, session.begin():
        run = Run(
            id=run_id,
            thread_id=thread_id,
            selected_source_ids=[source_id],
            config={"source_versions": {source_id: 1}},
        )
        session.add(run)
        session.flush()
        session.add(
            Evidence(
                id=evidence_id,
                run_id=run_id,
                kind="document",
                source_ids=[source_id],
                details={"source_versions": {source_id: 1}, "excerpt": "Original"},
            )
        )
    with sessions() as session:
        run = session.get(Run, run_id)
        undeclared = refs.answer(json.dumps({"text": "Claim [e1]"}))
        with pytest.raises(ValueError, match="no declared evidence"):
            validate_answer(session, run, undeclared)
        answer = refs.answer(json.dumps({"text": "Claim [e1]", "evidence_ids": ["e1"]}))
        validate_answer(session, run, answer)
        run.config = {"source_versions": {source_id: 2}}
        with pytest.raises(ValueError, match="another selected source version"):
            validate_answer(session, run, answer)
        run.selected_source_ids = []
        with pytest.raises(ValueError, match="unselected source"):
            validate_answer(session, run, answer)


async def test_python_stages_short_paths_and_retains_original_code(
    client_db, monkeypatch
):
    from types import SimpleNamespace
    import app.agent.runtime as runtime_module
    from app.agent.references import ModelReferences
    from app.contracts import ToolResult
    from app.db.models import Dataset, ToolCall
    from app.workers.queue import Claim

    _, sessions, (workspace_id, thread_id), root = client_db
    storage = FileStorage(root)
    with sessions() as session, session.begin():
        source = Source(
            workspace_id=workspace_id,
            kind="csv",
            display_name="sales.csv",
            state="ready",
        )
        session.add(source)
        session.flush()
        dataset = Dataset(
            source_id=source.id,
            source_version=1,
            identity="sales",
            schema_version="v1",
            details={},
        )
        run = Run(
            thread_id=thread_id,
            state="running",
            selected_source_ids=[source.id],
            config={"source_versions": {source.id: 1}},
        )
        session.add_all([dataset, run])
        session.flush()
        call = ToolCall(
            run_id=run.id,
            provider_call_id="python",
            name="run_python",
            input_reference={},
            decision="allowed",
            status="running",
        )
        stored = storage.put("derived/snapshot.csv", b"value\n42\n")
        artifact = Artifact(
            run_id=run.id,
            storage_key=stored.key,
            display_name="snapshot.csv",
            media_type="text/csv",
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            lineage=[source.id],
        )
        session.add_all([call, artifact])
        session.flush()
        run_id, dataset_id, artifact_id, tool_id = (
            run.id,
            dataset.id,
            artifact.id,
            call.id,
        )
    writes, programs = {}, []

    class Sandbox:
        async def write(self, guest_id, path, content):
            writes[path] = content

    class Python:
        async def _ensure_session(self):
            return SimpleNamespace(id="guest")

        async def execute(self, code, tool_id, output_paths, timeout_seconds):
            assert (
                writes["inputs/dataset_1.csv"]
                == writes["inputs/artifact_1"]
                == b"value\n42\n"
            )
            programs.append(code)
            return ToolResult(status="ok", summary="42")

    runtime = runtime_module.RunRuntime(
        Claim(str(uuid4()), "agent_run", str(uuid4()), {}, run_id, 1),
        Settings(
            _env_file=None, sandbox_base_url="http://sandbox", sandbox_image="test"
        ),
        sessions,
    )
    runtime.references = ModelReferences(
        {"dataset_1": dataset_id, "artifact_1": artifact_id}
    )
    runtime.python, runtime.sandbox = Python(), Sandbox()
    monkeypatch.setattr(
        runtime,
        "guard",
        lambda session, allow_cancelled=False: session.get(Run, run_id),
    )
    monkeypatch.setattr(runtime_module, "get_storage", lambda settings: storage)
    monkeypatch.setattr("app.sources.files.working_csv", lambda *args: b"value\n42\n")
    code = "print(open('/workspace/inputs/dataset_1.csv').read())\nprint(open('/workspace/inputs/artifact_1').read())"
    args = runtime_module.PythonInput(
        code=code, input_dataset_ids=[dataset_id], input_artifact_ids=[artifact_id]
    )
    result = await runtime.run_python(workspace_id, run_id, tool_id, args)
    assert programs == [code]
    assert writes[f"inputs/{dataset_id}.csv"] == writes["inputs/dataset_1.csv"]
    assert result.data["staged_input_artifacts"][0]["guest_aliases"] == [
        "/workspace/inputs/dataset_1.csv"
    ]
    with sessions() as session:
        retained = session.get(Artifact, result.data["code_artifact_id"])
        assert storage.read(retained.storage_key).decode() == code
