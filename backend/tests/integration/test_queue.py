import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import (
    Artifact,
    AuditEvent,
    Base,
    Document,
    Job,
    Run,
    Source,
    Thread,
    Workspace,
    now,
)
from app.db.repository import append_event
from app.workers.queue import Claim, LeaseLost, claim, finish, heartbeat, fail
from app.workers.main import maintenance
from app.storage.filesystem import FileStorage

pytestmark = pytest.mark.integration


@pytest.fixture
def db_factory():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = "test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    # Force creation in the new schema; public is visible only for pgvector.
    Base.metadata.create_all(engine, checkfirst=False)
    try:
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def enqueue(db_factory, count=1):
    with db_factory() as session, session.begin():
        for _ in range(count):
            session.add(Job(kind="verify_storage", dedupe_key=str(uuid4()), payload={}))


def test_two_workers_cannot_claim_same_job(db_factory):
    enqueue(db_factory)
    barrier = Barrier(2)

    def lease(owner):
        with db_factory() as session, session.begin():
            barrier.wait()
            return claim(session, owner, 60)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lease, ["one", "two"]))
    assert sum(result is not None for result in results) == 1


def test_expired_owner_cannot_finalize_or_heartbeat(db_factory):
    enqueue(db_factory)
    with db_factory() as session, session.begin():
        first = claim(session, "one", 60)
    with db_factory() as session, session.begin():
        session.execute(
            update(Job)
            .where(Job.id == first.id)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
        )
    with db_factory() as session, session.begin():
        second = claim(session, "two", 60)
    assert first.id == second.id and first.token != second.token
    for operation in [
        lambda s: finish(s, first.id, first.token, {}),
        lambda s: heartbeat(s, first.id, first.token, 60),
    ]:
        with db_factory() as session, session.begin(), pytest.raises(LeaseLost):
            operation(session)
    with db_factory() as session, session.begin():
        heartbeat(session, second.id, second.token, 60)
        finish(session, second.id, second.token, {"verified": True})


def test_retry_limit_and_delayed_retry(db_factory):
    enqueue(db_factory)
    with db_factory() as session, session.begin():
        leased = claim(session, "one", 60)
        fail(session, leased.id, leased.token, "retry", True)
    with db_factory() as session, session.begin():
        assert claim(session, "two", 60) is None
        session.execute(
            update(Job).values(
                attempts=3,
                state="running",
                lease_expires_at=func.now() - timedelta(seconds=1),
            )
        )
    with db_factory() as session, session.begin():
        assert claim(session, "two", 60) is None
        assert session.get(Job, leased.id).state == "failed"


@pytest.mark.parametrize("kind", ["ingest_document", "index_document"])
def test_exhausted_document_jobs_have_terminal_visible_status(db_factory, kind):
    with db_factory() as session, session.begin():
        workspace = Workspace(label="exhausted document")
        session.add(workspace)
        session.flush()
        source = Source(
            workspace_id=workspace.id, kind="pdf", display_name="fixture.pdf"
        )
        session.add(source)
        session.flush()
        document = Document(
            source_id=source.id,
            source_version=1,
            extractor_version="test",
            chunker_version="test",
            state="running",
            stage="indexing",
            progress=85,
        )
        session.add(document)
        session.flush()
        document_id = document.id
        session.add(
            Job(
                kind=kind,
                payload={"document_id": document_id},
                dedupe_key=str(uuid4()),
                state="running",
                attempts=3,
                max_attempts=3,
                lease_expires_at=func.now() - timedelta(seconds=1),
            )
        )
    with db_factory() as session, session.begin():
        assert claim(session, "replacement", 60) is None
    with db_factory() as session:
        document = session.get(Document, document_id)
        assert document.state == ("failed" if kind == "ingest_document" else "ready")
        assert document.stage == (
            "failed" if kind == "ingest_document" else "index_degraded"
        )
        assert document.progress == 100
        assert document.details["error"]["code"] == "retry_limit_exceeded"


def test_concurrent_event_sequences_are_unique(db_factory):
    with db_factory() as session, session.begin():
        workspace = Workspace(label="test")
        session.add(workspace)
        session.flush()
        thread = Thread(workspace_id=workspace.id, label="test")
        session.add(thread)
        session.flush()
        run = Run(thread_id=thread.id)
        session.add(run)
        session.flush()
        run_id = run.id

    def emit(_):
        with db_factory() as session, session.begin():
            return append_event(session, run_id, "progress", {}).sequence

    with ThreadPoolExecutor(4) as pool:
        sequences = list(pool.map(emit, range(12)))
    assert sorted(sequences) == list(range(1, 13))


def test_real_maintenance_dispatch_verifies_stored_bytes(
    db_factory, tmp_path, monkeypatch
):
    from app.config import Settings
    import app.workers.main as worker

    stored = FileStorage(tmp_path).put("derived/run/result.csv", b"value\n25000\n")
    with db_factory() as session, session.begin():
        artifact = Artifact(
            storage_key=stored.key,
            display_name="result.csv",
            media_type="text/csv",
            byte_size=stored.byte_size,
            sha256=stored.sha256,
        )
        session.add(artifact)
        session.flush()
        session.add(
            Job(
                kind="verify_storage",
                dedupe_key=str(uuid4()),
                payload={"artifact_id": artifact.id},
            )
        )
    monkeypatch.setattr(worker, "factory", lambda: db_factory)
    monkeypatch.setattr(
        worker, "get_settings", lambda: Settings(_env_file=None, storage_root=tmp_path)
    )
    with db_factory() as session, session.begin():
        task = claim(session, "test", 60)
    result = maintenance(task)
    assert result["verified"] is True
    with db_factory() as session, session.begin():
        finish(session, task.id, task.token, result)
    with db_factory() as session:
        assert session.get(Job, task.id).state == "completed"


def test_document_worker_audit_is_recorded_with_stable_codes_sqlite(monkeypatch):
    import app.workers.main as worker

    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        with sessions() as session, session.begin():
            workspace = Workspace(label="worker audit")
            session.add(workspace)
            session.flush()
            source = Source(
                workspace_id=workspace.id,
                kind="pdf",
                display_name="audit.pdf",
                version=2,
            )
            session.add(source)
            session.flush()
            document = Document(
                source_id=source.id,
                source_version=2,
                extractor_version="extractor-test-v2",
                chunker_version="chunker-test-v3",
                state="ready",
                stage="indexed",
            )
            session.add(document)
            session.flush()
            job = Job(
                kind="ingest_document",
                dedupe_key=str(uuid4()),
                payload={"document_id": document.id},
                state="running",
                lease_token="lease-token",
                lease_expires_at=now() + timedelta(minutes=2),
            )
            session.add(job)
            session.flush()
            task = Claim(job.id, job.kind, "lease-token", job.payload, None, 1)
            source_id = source.id
        monkeypatch.setattr(worker, "factory", lambda: sessions)

        worker._record_source_audit(
            task, decision="allowed", reason_code="completed", result={"state": "ready"}
        )
        worker._record_source_audit(
            task, decision="failed", reason_code="pdf_page_limit"
        )
        worker._record_source_audit(task, decision="cancelled", reason_code="cancelled")
        with sessions() as session:
            events = session.scalars(
                select(AuditEvent).where(AuditEvent.source_id == source_id)
            ).all()
        assert {event.reason_code for event in events} == {
            "completed",
            "pdf_page_limit",
            "cancelled",
        }
        assert all(event.action == "source.ingest" for event in events)
        assert all(event.details["source_version"] == 2 for event in events)
        assert all(
            event.details["extractor_version"] == "extractor-test-v2"
            for event in events
        )
    finally:
        engine.dispose()
