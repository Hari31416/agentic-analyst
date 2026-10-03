"""Upgrade existing data and exercise real PostgreSQL lifecycle constraints."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.config import Settings

pytestmark = pytest.mark.integration


def test_migration_backfills_owners_cascades_and_roundtrips(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = "test_lifecycle_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        # Shadow public.alembic_version so this upgrade never sees the app DB head.
        conn.execute(
            text(
                f'CREATE TABLE "{schema}".alembic_version (version_num varchar(32) PRIMARY KEY)'
            )
        )
    scoped_url = (
        make_url(url)
        .update_query_dict({"options": f"-csearch_path={schema},public"})
        .render_as_string(hide_password=False)
    )
    settings = Settings(_env_file=None, database_url=scoped_url)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    config = Config("alembic.ini")
    engine = create_engine(scoped_url)
    wid, sid, did, jid, cache_id = [str(uuid4()) for _ in range(5)]
    try:
        command.upgrade(config, "a8c204d39f51")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO workspaces (id,label,created_at) VALUES (:id,'old',now())"
                ),
                {"id": wid},
            )
            conn.execute(
                text(
                    """INSERT INTO sources (id,created_at,workspace_id,kind,version,display_name,state,details)
                VALUES (:id,now(),:wid,'pdf',1,'old.pdf','ready','{}')"""
                ),
                {"id": sid, "wid": wid},
            )
            conn.execute(
                text(
                    """INSERT INTO documents (id,created_at,source_id,source_version,extractor_version,chunker_version,state,stage,progress,details)
                VALUES (:id,now(),:sid,1,'v1','v1','ready','indexed',100,'{}')"""
                ),
                {"id": did, "sid": sid},
            )
            conn.execute(
                text(
                    """INSERT INTO jobs (id,created_at,kind,dedupe_key,payload,state,attempts,max_attempts,available_at)
                VALUES (:id,now(),'ingest_document',:id,jsonb_build_object('document_id',CAST(:did AS text)),'completed',1,3,now())"""
                ),
                {"id": jid, "did": did},
            )
            conn.execute(
                text(
                    """INSERT INTO summary_cache (id,created_at,fingerprint,scope,payload)
                VALUES (:id,now(),:id,'document',jsonb_build_object('documents',jsonb_build_array(jsonb_build_object('source_id',CAST(:sid AS text)))))"""
                ),
                {"id": cache_id, "sid": sid},
            )
        command.upgrade(config, "head")
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT workspace_id,document_id FROM jobs WHERE id=:id"),
                {"id": jid},
            ).one()
            assert tuple(row) == (wid, did)
            assert (
                conn.scalar(
                    text("SELECT workspace_id FROM summary_cache WHERE id=:id"),
                    {"id": cache_id},
                )
                == wid
            )
            inspector = inspect(conn)
            owned_tables = (
                "threads",
                "sources",
                "connections",
                "datasets",
                "runs",
                "messages",
                "jobs",
                "events",
                "tool_calls",
                "artifacts",
                "evidence",
                "audit_events",
                "documents",
                "document_blocks",
                "document_chunks",
                "index_generations",
                "chunk_embeddings",
                "summary_cache",
            )
            for table in owned_tables:
                assert all(
                    fk["options"].get("ondelete") == "CASCADE"
                    for fk in inspector.get_foreign_keys(table)
                )
        command.check(config)
        command.downgrade(config, "a8c204d39f51")
        with engine.connect() as conn:
            assert (
                inspect(conn).get_foreign_keys("sources")[0]["options"].get("ondelete")
                is None
            )
        command.upgrade(config, "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM workspaces WHERE id=:id"), {"id": wid})
        with engine.connect() as conn:
            for table in ("sources", "documents", "jobs", "summary_cache"):
                assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


# Reuse the isolated PostgreSQL fixture; no test touches the application workspace.
from tests.integration.test_queue import db_factory


def test_postgres_api_retention_purge_and_leased_cleanup(
    db_factory, tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from app.db.models import (
        Artifact,
        AuditEvent,
        Dataset,
        Document,
        Job,
        Message,
        Run,
        Source,
        Thread,
        Workspace,
    )
    from app.db.session import get_session
    from app.main import app
    from app.storage.filesystem import FileStorage
    from app.workers.queue import claim, finish
    import app.workers.main as worker

    storage = FileStorage(tmp_path)
    original = storage.put(f"originals/{uuid4()}/input.csv", b"count\n2\n")
    output = storage.put(f"derived/{uuid4()}/output.csv", b"total\n25000\n")
    with db_factory() as session, session.begin():
        workspace = Workspace(label="lifecycle")
        session.add(workspace)
        session.flush()
        thread = Thread(workspace_id=workspace.id, label="history")
        source = Source(
            workspace_id=workspace.id,
            kind="csv",
            display_name="input.csv",
            state="ready",
            storage_key=original.key,
        )
        session.add_all([thread, source])
        session.flush()
        run = Run(
            thread_id=thread.id,
            state="completed",
            selected_source_ids=[source.id],
            outcome={"cleanup": "complete"},
        )
        session.add(run)
        session.flush()
        artifact = Artifact(
            run_id=run.id,
            storage_key=output.key,
            display_name="output.csv",
            media_type="text/csv",
            byte_size=output.byte_size,
            sha256=output.sha256,
        )
        session.add_all(
            [
                artifact,
                Message(
                    run_id=run.id,
                    thread_id=thread.id,
                    role="assistant",
                    content="retained answer",
                ),
                AuditEvent(
                    run_id=run.id,
                    source_id=source.id,
                    action="test",
                    decision="allowed",
                    reason_code="test",
                    details={},
                ),
            ]
        )
        ids = workspace.id, thread.id, source.id, run.id

    def dependency():
        with db_factory() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    monkeypatch.setattr(worker, "factory", lambda: db_factory)
    monkeypatch.setattr(
        worker, "get_settings", lambda: Settings(_env_file=None, storage_root=tmp_path)
    )
    try:
        with TestClient(app) as client:
            response = client.delete(f"/api/sources/{ids[2]}")
            assert response.status_code == 200
            assert response.json()["retention"] == "archived_for_citations"
            assert storage.read(original.key) == b"count\n2\n"
            with db_factory() as session, session.begin():
                verify_job = Job(
                    kind="verify_storage",
                    dedupe_key=str(uuid4()),
                    payload={"artifact_id": artifact.id},
                )
                session.add(verify_job)
                session.flush()
                verify_job_id = verify_job.id
            assert client.delete(f"/api/threads/{ids[1]}").status_code == 409
            with db_factory() as session, session.begin():
                session.get(Job, verify_job_id).state = "completed"
            assert client.delete(f"/api/threads/{ids[1]}").status_code == 204
            with db_factory() as session:
                assert session.get(Run, ids[3]) is None
                assert session.get(Job, verify_job_id) is None
                assert session.scalar(select(AuditEvent)) is None
            response = client.delete(f"/api/sources/{ids[2]}")
            assert response.json()["retention"] == "purged"
            assert client.delete(f"/api/workspaces/{ids[0]}").status_code == 204
        # Outbox jobs survive parent deletion and the normal leased worker deletes bytes.
        for _ in range(2):
            with db_factory() as session, session.begin():
                task = claim(session, "cleanup-test", 60)
            assert task is not None and task.kind == "delete_storage"
            result = worker.maintenance(task)
            assert result == {"deleted": True}
            with db_factory() as session, session.begin():
                finish(session, task.id, task.token, result)
        with pytest.raises(FileNotFoundError):
            storage.read(original.key)
        with pytest.raises(FileNotFoundError):
            storage.read(output.key)
    finally:
        app.dependency_overrides.pop(get_session, None)
