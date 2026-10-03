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
