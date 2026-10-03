"""Workspace, thread, source, and document deletion contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest

from app.db.models import (
    Artifact,
    Base,
    Connection,
    Dataset,
    Document,
    Event,
    Job,
    Message,
    Run,
    Source,
    Thread,
    Workspace,
)
from app.db.session import get_session
from app.storage.filesystem import FileStorage
from app.storage.s3 import S3Storage, StorageUnavailable
from app.workers.queue import Claim, LeaseLost


def _fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    from app.main import app

    def dependency():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    return TestClient(app), sessions, engine, app


def _seed(
    sessions, *, source=True, run_state="completed", run_config=None, run_outcome=None
):
    with sessions() as session:
        workspace = Workspace(label="Research")
        session.add(workspace)
        session.flush()
        thread = Thread(workspace_id=workspace.id, label="Analysis")
        session.add(thread)
        session.flush()
        src = None
        if source:
            src = Source(
                workspace_id=workspace.id,
                kind="csv",
                display_name="input.csv",
                state="ready",
                storage_key="originals/input.csv",
                content_hash="hash",
                schema_version="csv-v1",
                details={},
            )
            session.add(src)
            session.flush()
        run = Run(
            thread_id=thread.id,
            state=run_state,
            selected_source_ids=[src.id] if src else [],
            config=run_config or {},
            outcome=run_outcome,
        )
        session.add(run)
        session.flush()
        session.add(
            Message(thread_id=thread.id, run_id=run.id, role="user", content="Question")
        )
        session.add(Event(run_id=run.id, sequence=1, type="started", payload={}))
        ids = {"workspace": workspace.id, "thread": thread.id, "run": run.id}
        if src:
            ids["source"] = src.id
        session.commit()
        return ids


def _close(client, engine, app):
    client.close()
    app.dependency_overrides.pop(get_session, None)
    engine.dispose()


def test_workspace_and_thread_labels_are_patchable_and_validated():
    client, sessions, engine, app = _fixture()
    try:
        ids = _seed(sessions, source=False)
        for path in (
            f"/api/workspaces/{ids['workspace']}",
            f"/api/threads/{ids['thread']}",
        ):
            response = client.patch(path, json={"label": "  Renamed  "})
            assert response.status_code == 200, response.text
            assert response.json()["label"] == "Renamed"
            assert client.patch(path, json={"label": "  "}).status_code == 422
            assert client.patch(path, json={"unexpected": "value"}).status_code == 422
        assert (
            client.patch(
                f"/api/workspaces/{uuid4()}", json={"label": "Missing"}
            ).status_code
            == 404
        )
        assert (
            client.patch(
                f"/api/threads/{uuid4()}", json={"label": "Missing"}
            ).status_code
            == 404
        )
    finally:
        _close(client, engine, app)


def test_thread_delete_cascades_owned_rows_but_rejects_active_run():
    client, sessions, engine, app = _fixture()
    try:
        ids = _seed(sessions, source=False, run_state="queued")
        assert client.delete(f"/api/threads/{ids['thread']}").status_code == 409
        with sessions.begin() as session:
            run = session.get(Run, ids["run"])
            run.state = "completed"
        response = client.delete(f"/api/threads/{ids['thread']}")
        assert response.status_code == 204, response.text
        with sessions() as session:
            assert session.get(Thread, ids["thread"]) is None
            assert (
                session.scalar(
                    select(func.count()).select_from(Run).where(Run.id == ids["run"])
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(Message)
                    .where(Message.thread_id == ids["thread"])
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(Event)
                    .where(Event.run_id == ids["run"])
                )
                == 0
            )
        assert client.delete(f"/api/threads/{ids['thread']}").status_code == 404
    finally:
        _close(client, engine, app)


def test_thread_delete_rejects_unconfirmed_sandbox_cleanup():
    client, sessions, engine, app = _fixture()
    try:
        ids = _seed(
            sessions,
            source=False,
            run_state="failed",
            run_config={"sandbox_session_id": "session-1"},
            run_outcome={"cleanup": "failed"},
        )
        response = client.delete(f"/api/threads/{ids['thread']}")
        assert response.status_code == 409
        with sessions() as session:
            assert session.get(Thread, ids["thread"]) is not None
    finally:
        _close(client, engine, app)


def test_thread_delete_rejects_artifact_referenced_by_source_or_dataset_lineage():
    client, sessions, engine, app = _fixture()
    try:
        ids = _seed(sessions)
        with sessions.begin() as session:
            artifact = Artifact(
                run_id=ids["run"],
                storage_key="derived/out.csv",
                display_name="out.csv",
                media_type="text/csv",
                byte_size=1,
                sha256="a" * 64,
                lineage=[],
                durable=True,
            )
            session.add(artifact)
            session.flush()
            retained = Source(
                workspace_id=ids["workspace"],
                kind="csv",
                display_name="derived.csv",
                state="ready",
                storage_key="derived/out.csv",
                content_hash="b" * 64,
                schema_version="csv-v1",
                details={"producer_run_id": ids["run"], "artifact_id": artifact.id},
            )
            session.add(retained)
            session.flush()
            session.add(
                Dataset(
                    source_id=ids["source"],
                    source_version=1,
                    identity="derived/out.csv",
                    schema_version="csv-v1",
                    details={},
                    storage_key="derived/out.csv",
                    designation="derived",
                    lineage=[f"artifact:{artifact.id}"],
                )
            )
        response = client.delete(f"/api/threads/{ids['thread']}")
        assert response.status_code == 409
        with sessions() as session:
            assert session.get(Thread, ids["thread"]) is not None
            assert session.get(Artifact, artifact.id) is not None
    finally:
        _close(client, engine, app)


def test_workspace_delete_removes_workspace_tree_and_queues_blob_cleanup():
    client, sessions, engine, app = _fixture()
    try:
        ids = _seed(sessions)
        with sessions.begin() as session:
            artifact = Artifact(
                run_id=ids["run"],
                storage_key="derived/out.csv",
                display_name="out.csv",
                media_type="text/csv",
                byte_size=1,
                sha256="a" * 64,
                lineage=[],
                durable=True,
            )
            session.add(artifact)
            session.add(
                Connection(
                    source_id=ids["source"],
                    dialect="postgresql",
                    host="db",
                    port=5432,
                    database_name="data",
                    username="analyst",
                    encrypted_credentials="encrypted",
                )
            )
        response = client.delete(f"/api/workspaces/{ids['workspace']}")
        assert response.status_code == 204, response.text
        with sessions() as session:
            for model, row_id in (
                (Workspace, ids["workspace"]),
                (Thread, ids["thread"]),
                (Run, ids["run"]),
                (Source, ids["source"]),
            ):
                assert session.get(model, row_id) is None
            cleanup = list(
                session.scalars(select(Job).where(Job.kind == "delete_storage"))
            )
            assert {job.payload["storage_key"] for job in cleanup} >= {
                "originals/input.csv",
                "derived/out.csv",
            }
            assert all(job.state == "queued" for job in cleanup)
        assert client.delete(f"/api/workspaces/{ids['workspace']}").status_code == 404
    finally:
        _close(client, engine, app)


def test_workspace_delete_rejects_active_ingestion_job():
    client, sessions, engine, app = _fixture()
    try:
        ids = _seed(sessions)
        with sessions.begin() as session:
            session.add(
                Job(
                    kind="ingest_document",
                    run_id=None,
                    dedupe_key="active-ingest",
                    payload={"workspace_id": ids["workspace"]},
                    state="running",
                )
            )
        response = client.delete(f"/api/workspaces/{ids['workspace']}")
        assert response.status_code == 409
        with sessions() as session:
            assert session.get(Workspace, ids["workspace"]) is not None
    finally:
        _close(client, engine, app)


def test_source_delete_hard_deletes_unused_source_and_archives_cited_source():
    client, sessions, engine, app = _fixture()
    try:
        ids = _seed(sessions)
        unused = Source(
            workspace_id=ids["workspace"],
            kind="csv",
            display_name="unused.csv",
            state="ready",
            storage_key="originals/unused.csv",
            content_hash="unused",
            schema_version="csv-v1",
            details={},
        )
        with sessions.begin() as session:
            session.add(unused)
        hard = client.delete(f"/api/sources/{unused.id}")
        assert hard.status_code == 200, hard.text
        assert hard.json() == {
            "source_id": unused.id,
            "state": "deleted",
            "retention": "purged",
        }
        with sessions() as session:
            assert session.get(Source, unused.id) is None
            assert session.get(Job, hard.json().get("job_id", "missing")) is None
            queued = session.scalar(
                select(Job).where(
                    Job.kind == "delete_storage",
                    Job.payload["storage_key"].as_string() == "originals/unused.csv",
                )
            )
            assert queued is not None and queued.state == "queued"
            connection = Connection(
                source_id=ids["source"],
                dialect="postgresql",
                host="db",
                port=5432,
                database_name="data",
                username="analyst",
                encrypted_credentials="secret",
            )
            session.add(connection)
            session.commit()
        soft = client.delete(
            f"/api/workspaces/{ids['workspace']}/sources/{ids['source']}"
        )
        assert soft.status_code == 200, soft.text
        assert soft.json() == {
            "source_id": ids["source"],
            "state": "deleted",
            "retention": "archived_for_citations",
        }
        with sessions() as session:
            source = session.get(Source, ids["source"])
            assert source is not None and source.state == "deleted"
            assert session.get(Connection, connection.id) is None
    finally:
        _close(client, engine, app)


def test_document_delete_cancels_queued_job_and_rejects_running_job():
    client, sessions, engine, app = _fixture()
    try:
        ids = _seed(sessions)
        with sessions.begin() as session:
            document = Document(
                source_id=ids["source"],
                source_version=1,
                extractor_version="v1",
                chunker_version="v1",
                state="queued",
                stage="queued",
                progress=0,
                details={},
            )
            session.add(document)
            session.flush()
            queued = Job(
                kind="ingest_document",
                run_id=None,
                dedupe_key="document-ingest",
                payload={"document_id": document.id},
                state="queued",
            )
            session.add(queued)
        response = client.delete(f"/api/documents/{document.id}")
        assert response.status_code == 200, response.text
        assert response.json()["retention"] == "archived_for_citations"
        with sessions() as session:
            assert session.get(Job, queued.id).state == "cancelled"
            assert session.get(Source, ids["source"]).state == "deleted"
            session.get(Job, queued.id).state = "failed"
            session.get(Document, document.id).state = "queued"
            session.get(Source, ids["source"]).state = "ready"
            running = Job(
                kind="index_document",
                run_id=None,
                dedupe_key="document-index",
                payload={"document_id": document.id},
                state="running",
            )
            session.add(running)
            session.commit()
        blocked = client.delete(f"/api/documents/{document.id}")
        assert blocked.status_code == 409
    finally:
        _close(client, engine, app)


def _maintenance_task(sessions, *, payload, lease_token=None, expires_in=3600):
    token = lease_token or str(uuid4())
    job_id = str(uuid4())
    with sessions.begin() as session:
        session.add(
            Job(
                id=job_id,
                kind="delete_storage",
                dedupe_key=f"delete-storage-test-{job_id}",
                payload=payload,
                state="running",
                lease_owner="test-worker",
                lease_token=token,
                lease_expires_at=datetime.now(timezone.utc)
                + timedelta(seconds=expires_in),
            )
        )
    return Claim(job_id, "delete_storage", token, payload, None, 1)


def _configure_storage_worker(monkeypatch, sessions, storage, tmp_path):
    from app.config import Settings
    import app.workers.main as worker

    monkeypatch.setattr(worker, "factory", lambda: sessions)
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: Settings(_env_file=None, storage_root=Path(tmp_path)),
    )
    monkeypatch.setattr(worker, "get_storage", lambda _settings: storage)
    return worker


def test_delete_storage_worker_requires_live_lease_and_deletes_idempotently(
    tmp_path, monkeypatch
):
    client, sessions, engine, app = _fixture()
    try:
        storage = FileStorage(tmp_path)
        key = "derived/lifecycle/result.csv"
        storage.put(key, b"result\n")
        worker = _configure_storage_worker(monkeypatch, sessions, storage, tmp_path)
        task = _maintenance_task(sessions, payload={"storage_key": key})

        assert worker.maintenance(task) == {"deleted": True}
        assert not storage.path(key).exists()
        # Retry after an acknowledged delete remains safe for the same live lease.
        assert worker.maintenance(task) == {"deleted": True}
    finally:
        _close(client, engine, app)


def test_delete_storage_worker_retains_keys_still_used_by_database_rows(
    tmp_path, monkeypatch
):
    client, sessions, engine, app = _fixture()
    try:
        ids = _seed(sessions)
        storage = FileStorage(tmp_path)
        key = "derived/shared/result.csv"
        storage.put(key, b"shared\n")
        with sessions.begin() as session:
            source = session.get(Source, ids["source"])
            source.storage_key = key
            session.add(
                Dataset(
                    source_id=source.id,
                    source_version=1,
                    identity="shared/result.csv",
                    schema_version="csv-v1",
                    details={},
                    storage_key=key,
                    designation="derived",
                    lineage=[],
                )
            )
            session.add(
                Artifact(
                    run_id=ids["run"],
                    storage_key=key,
                    display_name="result.csv",
                    media_type="text/csv",
                    byte_size=7,
                    sha256="a" * 64,
                    lineage=[],
                    durable=True,
                )
            )
        worker = _configure_storage_worker(monkeypatch, sessions, storage, tmp_path)
        task = _maintenance_task(sessions, payload={"storage_key": key})

        assert worker.maintenance(task) == {
            "deleted": False,
            "retained_shared_object": True,
        }
        assert storage.read(key) == b"shared\n"
    finally:
        _close(client, engine, app)


def test_delete_storage_worker_rejects_invalid_key_and_stale_lease(
    tmp_path, monkeypatch
):
    client, sessions, engine, app = _fixture()
    try:
        storage = FileStorage(tmp_path)
        worker = _configure_storage_worker(monkeypatch, sessions, storage, tmp_path)
        invalid = _maintenance_task(sessions, payload={"storage_key": "../escape"})
        with pytest.raises(ValueError, match="unsafe storage key"):
            worker.maintenance(invalid)

        key = "originals/lifecycle/input.csv"
        storage.put(key, b"input\n")
        stale = _maintenance_task(
            sessions,
            payload={"storage_key": key},
            lease_token="current-owner-token",
        )
        with sessions.begin() as session:
            session.get(Job, stale.id).lease_token = "new-owner-token"
        with pytest.raises(LeaseLost, match="ownership lost"):
            worker.maintenance(stale)
        assert storage.read(key) == b"input\n"
    finally:
        _close(client, engine, app)


def test_s3_delete_uses_key_is_missing_safe_and_wraps_service_failures():
    from botocore.exceptions import EndpointConnectionError

    class FakeS3:
        def __init__(self):
            self.keys = {"derived/cleanup/result.csv"}
            self.deleted = []
            self.fail = False

        def delete_object(self, *, Bucket, Key):
            assert Bucket == "lifecycle-tests"
            self.deleted.append(Key)
            if self.fail:
                raise EndpointConnectionError(endpoint_url="http://storage.invalid")
            self.keys.discard(Key)

    from app.config import Settings

    client = FakeS3()
    storage = S3Storage(
        Settings(
            _env_file=None,
            storage_backend="s3",
            s3_bucket="lifecycle-tests",
            s3_endpoint_url="http://storage.invalid",
            s3_access_key="test-access",
            s3_secret_key="test-secret",
        ),
        client=client,
    )
    storage.delete("derived/cleanup/result.csv")
    # S3 treats deleting an absent key as success, so worker retries are harmless.
    storage.delete("derived/cleanup/result.csv")
    assert client.deleted == [
        "derived/cleanup/result.csv",
        "derived/cleanup/result.csv",
    ]

    client.fail = True
    with pytest.raises(StorageUnavailable, match="S3 deletion failed"):
        storage.delete("derived/cleanup/result.csv")


@pytest.mark.parametrize("lineage_kind", ["source_details_document", "dataset_lineage"])
def test_source_delete_archives_derived_lineage_without_run_selection(
    lineage_kind, tmp_path
):
    client, sessions, engine, app = _fixture()
    try:
        ids = _seed(sessions, source=False)
        with sessions.begin() as session:
            source = Source(
                workspace_id=ids["workspace"],
                kind="csv",
                display_name="original.csv",
                state="ready",
                storage_key="originals/lineage.csv",
                content_hash="lineage-source",
                schema_version="csv-v1",
                details={},
            )
            sibling = Source(
                workspace_id=ids["workspace"],
                kind="csv",
                display_name="derived.csv",
                state="ready",
                storage_key="derived/lineage.csv",
                content_hash="lineage-derived",
                schema_version="csv-v1",
                details={},
            )
            session.add_all([source, sibling])
            session.flush()
            document = Document(
                source_id=source.id,
                source_version=1,
                extractor_version="extractor-v1",
                chunker_version="chunker-v1",
                state="ready",
                stage="indexed",
                progress=100,
                details={},
            )
            session.add(document)
            session.flush()
            if lineage_kind == "source_details_document":
                sibling.details = {"document_id": document.id}
            else:
                session.add(
                    Dataset(
                        source_id=sibling.id,
                        source_version=1,
                        identity="derived/lineage.csv",
                        schema_version="csv-v1",
                        details={},
                        storage_key=sibling.storage_key,
                        designation="derived",
                        lineage=[f"document:{document.id}"],
                    )
                )
            source_id = source.id
            document_id = document.id

        with sessions() as session:
            assert session.get(Run, ids["run"]).selected_source_ids == []

        response = client.delete(f"/api/sources/{source_id}")
        assert response.status_code == 200, response.text
        assert response.json() == {
            "source_id": source_id,
            "state": "deleted",
            "retention": "archived_for_citations",
        }
        with sessions() as session:
            retained = session.get(Source, source_id)
            assert retained is not None and retained.state == "deleted"
            assert session.get(Document, document_id).state == "deleted"
    finally:
        _close(client, engine, app)
