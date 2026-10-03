import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.audit import inspection
from app.audit.inspection import collect_entries, run_export
from app.api.audit import router as audit_router
from app.db.models import (
    Artifact,
    AuditEvent,
    Base,
    Evidence,
    Event,
    Message,
    Run,
    Source,
    Thread,
    Workspace,
)
from app.db.repository import append_event


def make_db():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def test_export_reconstructs_run_and_redacts_nested_secrets():
    engine, sessions = make_db()
    try:
        with sessions() as session, session.begin():
            workspace = Workspace(label="audit")
            session.add(workspace)
            session.flush()
            thread = Thread(workspace_id=workspace.id, label="audit")
            session.add(thread)
            session.flush()
            source = Source(
                workspace_id=workspace.id,
                kind="csv",
                display_name="sales.csv",
                version=3,
            )
            session.add(source)
            session.flush()
            run = Run(
                thread_id=thread.id,
                state="cancelled",
                selected_source_ids=[source.id],
                config={
                    "source_versions": {source.id: 3},
                    "connector": {
                        "password": "TOP_SECRET",
                        "nested": {"api_key": "KEY_SENTINEL"},
                    },
                },
                outcome={"partial": True},
            )
            session.add(run)
            session.flush()
            session.add(
                Message(
                    thread_id=thread.id,
                    run_id=run.id,
                    role="user",
                    content="What were sales?",
                    selected_source_ids=[source.id],
                    references={},
                )
            )
            session.add(
                Evidence(
                    run_id=run.id,
                    kind="sql",
                    source_ids=[source.id],
                    details={
                        "sql": "select sum(total) from sales",
                        "source_versions": {source.id: 3},
                    },
                )
            )
            session.add(
                Artifact(
                    run_id=run.id,
                    storage_key=f"run/{run.id}/x.csv",
                    display_name="x.csv",
                    media_type="text/csv",
                    byte_size=3,
                    sha256="a" * 64,
                    lineage=[source.id],
                    durable=True,
                )
            )
            append_event(
                session, run.id, "terminal", {"state": "cancelled", "partial": True}
            )
            session.add(
                AuditEvent(
                    run_id=run.id,
                    action="run.cancel",
                    decision="allowed",
                    reason_code="user_requested",
                    details={"auth": {"refresh_token": "REFRESH_SENTINEL"}},
                )
            )
            session.flush()
            result = run_export(session, run)
            serialized = str(result)
            assert result["schema_version"] == 1
            assert result["question"] == "What were sales?"
            assert result["run"]["config"]["source_versions"][source.id] == 3
            assert result["limits"]["policy_unrecorded"] is True
            assert "[redacted]" in serialized
            assert "TOP_SECRET" not in serialized
            assert "KEY_SENTINEL" not in serialized
            assert "REFRESH_SENTINEL" not in serialized
            assert (
                "connector" in serialized
            )  # key is safe; nested secret values are removed
    finally:
        engine.dispose()


def test_source_processing_audit_is_limited_to_selected_sources_and_filterable():
    engine, sessions = make_db()
    try:
        with sessions() as session, session.begin():
            workspace = Workspace(label="audit")
            session.add(workspace)
            session.flush()
            thread = Thread(workspace_id=workspace.id, label="audit")
            session.add(thread)
            session.flush()
            selected = Source(
                workspace_id=workspace.id, kind="pdf", display_name="selected.pdf"
            )
            other = Source(
                workspace_id=workspace.id, kind="pdf", display_name="other.pdf"
            )
            session.add_all([selected, other])
            session.flush()
            run = Run(
                thread_id=thread.id,
                selected_source_ids=[selected.id],
                state="completed",
            )
            session.add(run)
            session.flush()
            session.add_all(
                [
                    AuditEvent(
                        source_id=selected.id,
                        action="ingestion.extract",
                        decision="allowed",
                        reason_code="text_extracted",
                    ),
                    AuditEvent(
                        source_id=other.id,
                        action="ingestion.extract",
                        decision="rejected",
                        reason_code="bad_archive",
                    ),
                    AuditEvent(
                        run_id=run.id,
                        action="tool.run_sql",
                        decision="rejected",
                        reason_code="unsafe_sql",
                    ),
                ]
            )
            session.flush()
            entries = collect_entries(session, run)
            assert {entry["action"] for entry in entries} == {
                "ingestion.extract",
                "tool.run_sql",
            }
            assert (
                len(
                    [
                        entry
                        for entry in entries
                        if entry["action"] == "ingestion.extract"
                    ]
                )
                == 1
            )
            assert (
                collect_entries(session, run, action="tool.run_sql", state="rejected")[
                    0
                ]["details"]["reason_code"]
                == "unsafe_sql"
            )
    finally:
        engine.dispose()


def test_export_has_global_byte_cap_and_reports_truncation():
    engine, sessions = make_db()
    try:
        with sessions() as session, session.begin():
            workspace = Workspace(label="large audit")
            session.add(workspace)
            session.flush()
            thread = Thread(workspace_id=workspace.id, label="large audit")
            session.add(thread)
            session.flush()
            run = Run(
                thread_id=thread.id,
                state="failed",
                config={
                    "nested": {
                        "values": ["SENTINEL_START" + "x" * 100_000 for _ in range(300)]
                    }
                },
            )
            session.add(run)
            session.flush()
            result = run_export(session, run)
            encoded = json.dumps(
                result, ensure_ascii=False, separators=(",", ":")
            ).encode()
            assert len(encoded) <= 2 * 1024 * 1024
            assert result["limits"]["byte_budget_truncated"] is True
            assert result["limits"]["truncated_content_fields"] > 0
            assert "SENTINEL_START" in str(result)
    finally:
        engine.dispose()


def test_export_removes_configured_sentinel_before_truncating(monkeypatch):
    engine, sessions = make_db()
    monkeypatch.setattr(
        inspection, "configured_secrets", lambda: ("CONFIG_SENTINEL_123",)
    )
    try:
        with sessions() as session, session.begin():
            workspace = Workspace(label="sentinel")
            session.add(workspace)
            session.flush()
            thread = Thread(workspace_id=workspace.id, label="sentinel")
            session.add(thread)
            session.flush()
            run = Run(
                thread_id=thread.id,
                state="failed",
                outcome={"detail": "prefix CONFIG_SENTINEL_123 suffix"},
            )
            session.add(run)
            session.flush()
            exported = str(run_export(session, run))
            assert "CONFIG_SENTINEL_123" not in exported
            assert "[redacted]" in exported
    finally:
        engine.dispose()


def test_audit_endpoints_filter_page_and_download_json():
    engine, sessions = make_db()
    app = FastAPI()
    app.include_router(audit_router)

    def dependency():
        with sessions() as session:
            yield session

    from app.db.session import get_session

    app.dependency_overrides[get_session] = dependency
    try:
        with sessions() as session, session.begin():
            workspace = Workspace(label="endpoint")
            session.add(workspace)
            session.flush()
            thread = Thread(workspace_id=workspace.id, label="endpoint")
            session.add(thread)
            session.flush()
            run = Run(thread_id=thread.id, state="cancelled")
            session.add(run)
            session.flush()
            append_event(session, run.id, "terminal", {"state": "cancelled"})
            run_id = run.id

        client = TestClient(app)
        page = client.get(f"/api/runs/{run_id}/audit?state=cancelled&limit=1")
        assert page.status_code == 200
        assert page.json()["entries"][0]["kind"] == "event"
        assert page.json()["next_cursor"] is None
        exported = client.get(f"/api/runs/{run_id}/audit/export")
        assert exported.status_code == 200
        assert exported.headers["content-disposition"].startswith("attachment;")
        assert exported.json()["schema_version"] == 1
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
