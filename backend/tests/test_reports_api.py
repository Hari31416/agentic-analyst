"""Report selection, ownership, frozen retention, CRUD and version contracts."""

import hashlib
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.security import require_auth
from app.config import Settings, get_settings
from app.db.models import (
    Artifact,
    Base,
    Evidence,
    Job,
    Message,
    Pin,
    Report,
    ReportAsset,
    ReportVersion,
    Run,
    Thread,
    User,
    Workspace,
    now,
)
from app.db.session import get_session
from app.main import app
from app.storage.filesystem import FileStorage


@pytest.fixture
def report_client(tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    storage = FileStorage(tmp_path)
    settings = Settings(
        _env_file=None, storage_backend="filesystem", storage_root=tmp_path
    )
    with sessions() as session:
        users = [
            User(username=name, password_hash="unused") for name in ("alice", "bob")
        ]
        workspace = Workspace(label="Reports")
        other = Workspace(label="Other")
        session.add_all([*users, workspace, other])
        session.flush()
        thread = Thread(workspace_id=workspace.id, label="Sales")
        session.add(thread)
        session.flush()
        run = Run(
            thread_id=thread.id,
            state="completed",
            config={"source_versions": {"old-source": 1}},
        )
        session.add(run)
        session.flush()
        question = Message(
            thread_id=thread.id, run_id=run.id, role="user", content="Revenue?"
        )
        answer = Message(
            thread_id=thread.id,
            run_id=run.id,
            role="assistant",
            content="Revenue was 42.",
        )
        stored = storage.put(
            "derived/test/sales.csv", b"region,sales\nNorth,42\nSouth,12\n"
        )
        artifact = Artifact(
            run_id=run.id,
            role="output",
            storage_key=stored.key,
            display_name="sales.csv",
            media_type="text/csv",
            sha256=stored.sha256,
            byte_size=stored.byte_size,
        )
        session.add_all([question, answer, artifact])
        session.flush()
        pins = [
            Pin(
                user_id=users[0].id,
                thread_id=thread.id,
                kind=kind,
                target_id=target,
                title=kind,
                message_id=answer.id if kind == "message" else None,
                artifact_id=artifact.id if kind == "artifact" else None,
            )
            for kind, target in (
                ("thread", thread.id),
                ("message", answer.id),
                ("artifact", artifact.id),
            )
        ]
        session.add_all(pins)
        session.commit()
        ids = {
            "workspace": workspace.id,
            "other": other.id,
            "thread": thread.id,
            "run": run.id,
            "answer": answer.id,
            "question": question.id,
            "artifact": artifact.id,
            **{f"pin_{p.kind}": p.id for p in pins},
        }
        ids["thread_resource"] = thread.id
    previous = dict(app.dependency_overrides)

    def database():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = database
    app.dependency_overrides[get_settings] = lambda: settings
    # Reports uses get_settings directly, matching the storage factory convention.
    import app.api.reports as api_reports

    previous_settings = api_reports.get_settings
    api_reports.get_settings = lambda: settings
    app.dependency_overrides[require_auth] = lambda: users[0]
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, sessions, ids, users, storage
    api_reports.get_settings = previous_settings
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    engine.dispose()


def create(client, ids, kinds=("artifact",), **fields):
    return client.post(
        f"/api/workspaces/{ids['workspace']}/reports",
        json={
            "title": "Findings",
            "pin_ids": [ids[f"pin_{kind}"] for kind in kinds],
            **fields,
        },
    )


def ready(sessions, report, storage):
    version_id = report["versions"][0]["id"]
    with sessions() as session:
        version = session.get(ReportVersion, version_id)
        version.state = "ready"
        version.document = {
            "title": "Findings",
            "language": "en-IN",
            "sections": [
                {
                    "id": "summary",
                    "heading": "Summary",
                    "blocks": [
                        {
                            "id": "table",
                            "type": "table",
                            "artifact_id": next(iter(version.snapshot["assets"])),
                            "caption": "Revenue",
                            "columns": [],
                            "max_rows": 30,
                        }
                    ],
                }
            ],
        }
        stored = storage.put(f"derived/test/{version_id}.pdf", b"%PDF-test")
        version.pdf_key, version.pdf_sha256, version.pdf_size = (
            stored.key,
            stored.sha256,
            stored.byte_size,
        )
        for job in session.scalars(select(Job).where(Job.kind == "report_generation")):
            job.state = "completed"
        session.commit()
    return version_id


def test_artifact_only_freezes_bytes_without_conversation(report_client):
    client, sessions, ids, _, storage = report_client
    response = create(client, ids)
    assert response.status_code == 201, response.text
    report = response.json()
    with sessions() as session:
        version = session.get(ReportVersion, report["versions"][0]["id"])
        assert version.snapshot["messages"] == []
        assert list(version.snapshot["assets"]) == [ids["artifact"]]
        copied = version.snapshot["assets"][ids["artifact"]]
        assert (
            copied["storage_key"] != session.get(Artifact, ids["artifact"]).storage_key
        )
        job = session.scalar(select(Job).where(Job.kind == "report_generation"))
        assert job.payload == {"version_id": version.id}
        job.state = "completed"
        session.commit()
    assert client.delete(f"/api/pins/{ids['pin_artifact']}").status_code == 204
    assert client.delete(f"/api/threads/{ids['thread_resource']}").status_code == 204
    assert client.get(f"/api/reports/{report['id']}").status_code == 200
    download = client.get(
        f"/api/reports/{report['id']}/versions/{report['versions'][0]['id']}/assets/{ids['artifact']}"
    )
    assert download.content == b"region,sales\nNorth,42\nSouth,12\n"
    assert storage.read(copied["storage_key"]) == download.content


def test_ordered_overlap_selection_deduplicates_messages(report_client):
    client, sessions, ids, _, _ = report_client
    response = create(client, ids, ("message", "thread", "artifact", "message"))
    assert response.status_code == 201, response.text
    with sessions() as session:
        version = session.get(ReportVersion, response.json()["versions"][0]["id"])
        assert [item["id"] for item in version.snapshot["messages"]] == [
            ids["question"],
            ids["answer"],
        ]
        assert [p["kind"] for p in version.snapshot["selection"]] == [
            "message",
            "thread",
            "artifact",
        ]
        assert version.snapshot["runs"][0]["source_versions"] == {"old-source": 1}
        assert len(version.snapshot["assets"]) == 1


def test_report_crud_and_version_downloads(report_client):
    client, sessions, ids, _, storage = report_client
    report = create(client, ids).json()
    rid = report["id"]
    vid = report["versions"][0]["id"]
    assert client.get(f"/api/reports/{rid}/versions/{vid}/download").status_code == 409
    assert (
        client.post(
            f"/api/reports/{rid}/regenerate", json={"version_id": vid}
        ).status_code
        == 409
    )
    ready(sessions, report, storage)
    assert (
        client.get(f"/api/reports/{rid}/versions/{vid}/download").content
        == b"%PDF-test"
    )
    assert (
        client.get(f"/api/reports/{rid}/versions/{vid}/document").json()["sections"][0][
            "blocks"
        ][0]["artifact_id"]
        == ids["artifact"]
    )
    assert (
        client.patch(f"/api/reports/{rid}", json={"title": "Renamed"}).json()["title"]
        == "Renamed"
    )
    regenerated = client.post(
        f"/api/reports/{rid}/regenerate",
        json={"version_id": vid, "feedback": "Shorten it", "mode": "wording"},
    )
    assert regenerated.status_code == 201, regenerated.text
    versions = regenerated.json()["versions"]
    assert [v["number"] for v in versions] == [2, 1]
    with sessions() as session:
        old = session.get(ReportVersion, vid)
        new = session.get(ReportVersion, versions[0]["id"])
        assert new.snapshot == old.snapshot
        assert new.base_version_id == old.id
        assert old.state == "ready"
    assert (
        client.post(
            f"/api/reports/{rid}/regenerate", json={"version_id": vid}
        ).status_code
        == 409
    )
    assert len(client.get(f"/api/workspaces/{ids['workspace']}/reports").json()) == 1
    with sessions() as session:
        keys = {a.storage_key for a in session.scalars(select(ReportAsset))}
        keys.update(
            key for key in session.scalars(select(ReportVersion.pdf_key)) if key
        )
    assert client.delete(f"/api/reports/{rid}").status_code == 204
    assert client.get(f"/api/reports/{rid}").status_code == 404
    with sessions() as session:
        assert session.scalar(select(ReportAsset)) is None
        cleanup = {
            j.payload["storage_key"]
            for j in session.scalars(select(Job).where(Job.kind == "delete_storage"))
        }
        assert cleanup == keys
        assert session.scalar(select(Artifact)) is not None


def test_owner_and_workspace_isolation(report_client):
    client, sessions, ids, users, storage = report_client
    report = create(client, ids).json()
    rid = report["id"]
    vid = ready(sessions, report, storage)
    assert create(client, {**ids, "workspace": ids["other"]}).status_code == 404
    app.dependency_overrides[require_auth] = lambda: users[1]
    assert client.get(f"/api/workspaces/{ids['workspace']}/reports").json() == []
    assert create(client, ids).status_code == 404
    for path in (
        f"/api/reports/{rid}",
        f"/api/reports/{rid}/versions/{vid}/download",
        f"/api/reports/{rid}/versions/{vid}/document",
        f'/api/reports/{rid}/versions/{vid}/assets/{ids["artifact"]}',
    ):
        assert client.get(path).status_code == 404
    assert (
        client.patch(f"/api/reports/{rid}", json={"title": "Steal"}).status_code == 404
    )
    assert client.delete(f"/api/reports/{rid}").status_code == 404
    assert (
        client.post(
            f"/api/reports/{rid}/regenerate", json={"version_id": vid}
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"title": "x" * 201},
        {"language": "ar"},
        {"instructions": "x" * 4001},
        {"pin_ids": []},
        {"pin_ids": [str(uuid4())] * 31},
        {"unknown": True},
    ],
)
def test_create_validation(report_client, fields):
    client, _, ids, _, _ = report_client
    assert create(client, ids, **fields).status_code == 422


@pytest.mark.parametrize(
    "fields", [{}, {"title": None}, {"title": ""}, {"title": "  "}]
)
def test_optional_title_uses_selected_material(report_client, fields):
    client, sessions, ids, _, _ = report_client
    response = client.post(
        f"/api/workspaces/{ids['workspace']}/reports",
        json={"pin_ids": [ids["pin_artifact"]], **fields},
    )
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["title"] == "Report: artifact"
    with sessions() as session:
        version = session.get(ReportVersion, report["versions"][0]["id"])
        assert version.snapshot["title"] == report["title"]


@pytest.mark.parametrize("legacy", [False, True])
def test_report_evidence_excludes_search_traces(report_client, legacy):
    client, sessions, ids, _, _ = report_client
    with sessions() as session:
        cited = Evidence(
            run_id=ids["run"],
            kind="document",
            source_ids=[],
            details={
                "excerpt": "The original supporting passage.",
                "source_version": 3,
                "location": {"page": 42},
                "trace": {"diagnostics": "x" * 150_000},
            },
        )
        session.add(cited)
        session.flush()
        if not legacy:
            session.get(Message, ids["answer"]).references = {
                "evidence_ids": [cited.id]
            }
            session.add_all(
                [
                    Evidence(
                        run_id=ids["run"],
                        kind="document",
                        source_ids=[],
                        details={"excerpt": "Uncited search result."},
                    )
                    for _ in range(205)
                ]
            )
        session.commit()
        cited_id = cited.id
    response = create(client, ids, kinds=("message",))
    assert response.status_code == 201, response.text
    with sessions() as session:
        version = session.get(ReportVersion, response.json()["versions"][0]["id"])
        assert version.snapshot["evidence"] == [
            {
                "id": cited_id,
                "kind": "document",
                "source_ids": [],
                "details": {
                    "excerpt": "The original supporting passage.",
                    "source_version": 3,
                    "location": {"page": 42},
                },
            }
        ]
        assert "trace" in session.get(Evidence, cited_id).details


def test_evidence_limit_explains_actual_content_size(report_client):
    client, sessions, ids, _, _ = report_client
    with sessions() as session:
        evidence = Evidence(
            run_id=ids["run"],
            kind="document",
            source_ids=[],
            details={"excerpt": "x" * 100_001},
        )
        session.add(evidence)
        session.flush()
        session.get(Message, ids["answer"]).references = {"evidence_ids": [evidence.id]}
        session.commit()
    response = create(client, ids, kinds=("message",))
    assert response.status_code == 422
    assert "cite 1 evidence records" in response.json()["detail"]
    assert "100,000 evidence characters" in response.json()["detail"]


def test_integrity_failure_rolls_back_copies(report_client):
    client, sessions, ids, _, storage = report_client
    with sessions() as session:
        session.get(Artifact, ids["artifact"]).sha256 = "0" * 64
        session.commit()
    response = create(client, ids)
    assert response.status_code == 422, response.text
    with sessions() as session:
        assert session.scalar(select(Report)) is None
        assert session.scalar(select(ReportAsset)) is None
    assert not list(storage.root.glob("derived/*/reports/*/assets/*"))


def test_workspace_deletion_queues_report_cleanup(report_client):
    client, sessions, ids, _, storage = report_client
    report = create(client, ids).json()
    ready(sessions, report, storage)
    with sessions() as session:
        asset_keys = set(session.scalars(select(ReportAsset.storage_key)))
        pdf_keys = set(session.scalars(select(ReportVersion.pdf_key)))
    assert client.delete(f"/api/workspaces/{ids['workspace']}").status_code == 204
    with sessions() as session:
        assert session.scalar(select(Report)) is None
        keys = {
            j.payload["storage_key"]
            for j in session.scalars(select(Job).where(Job.kind == "delete_storage"))
        }
        assert asset_keys | pdf_keys <= keys


def test_active_turn_cannot_be_snapshotted(report_client):
    client, sessions, ids, _, _ = report_client
    with sessions() as session:
        session.get(Run, ids["run"]).state = "running"
        session.commit()
    assert create(client, ids, ("message",)).status_code == 409


def test_retained_table_preview_is_exact_scoped_and_bounded(report_client):
    client, sessions, ids, users, storage = report_client
    report = create(client, ids).json()
    vid = ready(sessions, report, storage)
    path = f"/api/reports/{report['id']}/versions/{vid}/assets/{ids['artifact']}/table"
    response = client.get(path + "?max_rows=1&columns=sales")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "columns": ["sales"],
        "rows": [["42"]],
        "truncated": True,
    }
    assert client.get(path + "?columns=missing").status_code == 422
    assert client.get(path + "?max_rows=101").status_code == 422
    app.dependency_overrides[require_auth] = lambda: users[1]
    assert client.get(path).status_code == 404


def test_cleanup_worker_retains_live_reports_then_deletes_copies(
    report_client, monkeypatch
):
    from app.workers import main as worker
    from app.workers.queue import Claim

    client, sessions, ids, _, storage = report_client
    report = create(client, ids).json()
    ready(sessions, report, storage)
    monkeypatch.setattr(worker, "factory", lambda: sessions)
    monkeypatch.setattr(worker, "get_storage", lambda _: storage)
    with sessions() as session:
        key = session.scalar(select(ReportAsset.storage_key))
        job = Job(
            kind="delete_storage",
            payload={"storage_key": key},
            dedupe_key="test-cleanup",
            state="running",
            lease_token="test-token",
            lease_expires_at=now() + timedelta(minutes=5),
        )
        session.add(job)
        session.commit()
        task = Claim(job.id, job.kind, job.lease_token, job.payload, None, 1)
    assert worker.maintenance(task) == {
        "deleted": False,
        "retained_shared_object": True,
    }
    assert storage.read(key)
    assert client.delete(f"/api/reports/{report['id']}").status_code == 204
    assert worker.maintenance(task) == {"deleted": True}
    with pytest.raises(FileNotFoundError):
        storage.read(key)
