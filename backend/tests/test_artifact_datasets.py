from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.artifact_datasets import router
from app.config import Settings
from app.db.models import Artifact, Base, Dataset, Run, Source, Thread, Workspace
from app.db.session import get_session
from app.storage.filesystem import FileStorage


def _fixture(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def dependency():
        with sessions() as session:
            yield session

    settings = Settings(_env_file=None, storage_root=tmp_path)
    storage = FileStorage(tmp_path)
    monkeypatch.setattr("app.api.artifacts.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.artifacts.get_storage", lambda _settings: storage)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = dependency
    with sessions() as session:
        workspace = Workspace(label="Research")
        session.add(workspace)
        session.flush()
        source = Source(
            workspace_id=workspace.id,
            kind="csv",
            display_name="input.csv",
            state="ready",
            storage_key="originals/input.csv",
            content_hash="source-hash",
            schema_version="file-profile-v1",
            details={},
        )
        session.add(source)
        session.flush()
        thread = Thread(workspace_id=workspace.id, label="Analysis")
        session.add(thread)
        session.flush()
        run = Run(
            thread_id=thread.id,
            state="completed",
            selected_source_ids=[source.id],
            config={"source_versions": {source.id: source.version}},
            outcome={},
        )
        session.add(run)
        session.flush()
        content = b"account,amount\n00123,25000.00\n00124,0.10\n"
        stored = storage.put(f"derived/{workspace.id}/{run.id}/result.csv", content)
        artifact = Artifact(
            run_id=run.id,
            storage_key=stored.key,
            display_name="cleaned.csv",
            media_type="text/csv",
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            lineage=[source.id, f"source:{source.id}@{source.version}"],
            durable=True,
        )
        session.add(artifact)
        session.commit()
        ids = {
            "artifact": artifact.id,
            "source": source.id,
            "workspace": workspace.id,
        }
    return TestClient(app), sessions, ids, engine


def test_register_csv_artifact_is_idempotent_and_retains_lineage(tmp_path, monkeypatch):
    client, sessions, ids, engine = _fixture(tmp_path, monkeypatch)
    try:
        response = client.post(f"/api/artifacts/{ids['artifact']}/dataset")
        assert response.status_code == 201, response.text
        created = response.json()
        assert created["display_name"] == "cleaned.csv"
        assert created["reused"] is False
        assert len(created["dataset_ids"]) == 1

        repeated = client.post(f"/api/artifacts/{ids['artifact']}/dataset")
        assert repeated.status_code == 201, repeated.text
        assert repeated.json()["source_id"] == created["source_id"]
        assert repeated.json()["dataset_ids"] == created["dataset_ids"]
        assert repeated.json()["reused"] is True

        with sessions() as session:
            source = session.get(Source, created["source_id"])
            dataset = session.get(Dataset, created["dataset_ids"][0])
            assert source is not None
            assert source.storage_key.endswith("result.csv")
            assert source.details["artifact_id"] == ids["artifact"]
            assert dataset is not None
            assert dataset.designation == "derived"
            assert f"artifact:{ids['artifact']}" in dataset.lineage
            assert (
                len(
                    list(
                        session.scalars(
                            select(Source).where(
                                Source.details["designation"].as_string() == "derived"
                            )
                        )
                    )
                )
                == 1
            )
    finally:
        client.close()
        engine.dispose()


def test_register_rejects_stale_source_lineage(tmp_path, monkeypatch):
    client, sessions, ids, engine = _fixture(tmp_path, monkeypatch)
    try:
        with sessions.begin() as session:
            source = session.get(Source, ids["source"])
            assert source is not None
            source.state = "deleted"
        response = client.post(f"/api/artifacts/{ids['artifact']}/dataset")
        assert response.status_code == 409
        assert "lineage" in response.json()["detail"].lower()
        with sessions() as session:
            assert (
                session.scalar(
                    select(Source).where(
                        Source.details["designation"].as_string() == "derived"
                    )
                )
                is None
            )
    finally:
        client.close()
        engine.dispose()


def test_register_rejects_changed_source_version(tmp_path, monkeypatch):
    client, sessions, ids, engine = _fixture(tmp_path, monkeypatch)
    try:
        with sessions.begin() as session:
            source = session.get(Source, ids["source"])
            assert source is not None
            source.version += 1
        response = client.post(f"/api/artifacts/{ids['artifact']}/dataset")
        assert response.status_code == 409
        assert "stale" in response.json()["detail"].lower()
    finally:
        client.close()
        engine.dispose()


def test_register_rejects_non_csv_artifact(tmp_path, monkeypatch):
    client, sessions, ids, engine = _fixture(tmp_path, monkeypatch)
    try:
        with sessions.begin() as session:
            artifact = session.get(Artifact, ids["artifact"])
            assert artifact is not None
            artifact.media_type = "application/pdf"
            artifact.display_name = "report.pdf"
        response = client.post(f"/api/artifacts/{ids['artifact']}/dataset")
        assert response.status_code == 415
    finally:
        client.close()
        engine.dispose()
