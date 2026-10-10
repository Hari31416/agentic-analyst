"""Personal pin CRUD, provenance, validation, and target lifecycle contracts."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth.security import require_auth
from app.db.models import Artifact, Base, Message, Pin, Run, Thread, User, Workspace
from app.db.session import get_session
from app.main import app


@pytest.fixture
def pins_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as session:
        users = [
            User(username=name, password_hash="unused") for name in ("alice", "bob")
        ]
        workspaces = [Workspace(label=name) for name in ("Research", "Other")]
        session.add_all([*users, *workspaces])
        session.flush()
        thread = Thread(workspace_id=workspaces[0].id, label="Sales")
        session.add(thread)
        session.flush()
        run = Run(thread_id=thread.id, state="completed")
        session.add(run)
        session.flush()
        question = Message(
            thread_id=thread.id, run_id=run.id, role="user", content="Sales?"
        )
        answer = Message(
            thread_id=thread.id,
            run_id=run.id,
            role="assistant",
            content="Sales are 42",
            references={"artifact_ids": []},
        )
        artifact = Artifact(
            run_id=run.id,
            storage_key="outputs/chart.png",
            display_name="chart.png",
            media_type="image/png",
            byte_size=42,
            sha256="a" * 64,
            role="output",
        )
        hidden = Artifact(
            run_id=run.id,
            storage_key="outputs/temp.png",
            display_name="temp.png",
            media_type="image/png",
            byte_size=1,
            sha256="b" * 64,
            durable=False,
        )
        session.add_all([question, answer, artifact, hidden])
        session.commit()
        ids = {
            "workspace": workspaces[0].id,
            "other": workspaces[1].id,
            "thread": thread.id,
            "message": answer.id,
            "question": question.id,
            "artifact": artifact.id,
            "hidden": hidden.id,
        }
    previous = dict(app.dependency_overrides)

    def database():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = database
    app.dependency_overrides[require_auth] = lambda: users[0]
    client = TestClient(app)
    try:
        yield client, sessions, ids, users
    finally:
        client.close()
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    engine.dispose()


def create(client, ids, kind, **fields):
    return client.post(
        f"/api/workspaces/{ids['workspace']}/pins",
        json={"kind": kind, "target_id": ids[kind], "title": f"Saved {kind}", **fields},
    )


@pytest.mark.parametrize("kind", ["thread", "message", "artifact"])
def test_full_crud(pins_client, kind):
    client, sessions, ids, _ = pins_client
    response = create(
        client, ids, kind, tags=[" sales ", "sales", ""], notes="Keep this"
    )
    assert response.status_code == 201, response.text
    pin = response.json()
    assert pin["tags"] == ["sales"]
    assert pin["thread_id"] == ids["thread"]
    assert pin["workspace_id"] == ids["workspace"]
    listing = client.get(f"/api/workspaces/{ids['workspace']}/pins").json()
    assert listing == [pin]
    detail = client.get(f"/api/pins/{pin['id']}").json()
    if kind == "artifact":
        assert detail["artifact"]["id"] == ids["artifact"]
        assert "messages" not in detail
        assert "storage_key" not in detail["artifact"]
    elif kind == "message":
        assert {message["id"] for message in detail["messages"]} == {
            ids["message"],
            ids["question"],
        }
    else:
        assert "artifact" not in detail and "messages" not in detail
    changed = client.patch(
        f"/api/pins/{pin['id']}",
        json={"title": " Renamed ", "notes": "Revised", "tags": [" new ", "new"]},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["title"] == "Renamed"
    assert changed.json()["notes"] == "Revised"
    assert changed.json()["tags"] == ["new"]
    assert (
        client.patch(f"/api/pins/{pin['id']}", json={"notes": ""}).json()["title"]
        == "Renamed"
    )
    assert create(client, ids, kind).status_code == 409
    assert client.delete(f"/api/pins/{pin['id']}").status_code == 204
    assert client.get(f"/api/pins/{pin['id']}").status_code == 404
    assert client.get(f"/api/workspaces/{ids['workspace']}/pins").json() == []
    with sessions() as session:
        assert session.get(Thread, ids["thread"]) is not None
        assert session.get(Message, ids["message"]) is not None
        assert session.get(Artifact, ids["artifact"]) is not None
    assert create(client, ids, kind).status_code == 201


def test_owner_isolation(pins_client):
    client, _, ids, users = pins_client
    pin = create(client, ids, "artifact").json()
    app.dependency_overrides[require_auth] = lambda: users[1]
    assert client.get(f"/api/workspaces/{ids['workspace']}/pins").json() == []
    for method in ["get", "patch", "delete"]:
        kwargs = {"json": {"title": "Steal"}} if method == "patch" else {}
        assert (
            getattr(client, method)(f"/api/pins/{pin['id']}", **kwargs).status_code
            == 404
        )
    # Another owner can independently pin the same artifact.
    assert create(client, ids, "artifact").status_code == 201


def test_target_validation_and_scoped_listing(pins_client):
    client, _, ids, _ = pins_client
    for kind in ("thread", "message", "artifact"):
        assert create(client, {**ids, kind: str(uuid4())}, kind).status_code == 404
        assert (
            create(client, {**ids, "workspace": ids["other"]}, kind).status_code == 404
        )
        assert create(client, ids, kind).status_code == 201
    assert (
        create(client, {**ids, "artifact": ids["hidden"]}, "artifact").status_code
        == 404
    )
    base = f"/api/workspaces/{ids['workspace']}/pins"
    assert len(client.get(base + "?kind=artifact").json()) == 1
    assert len(client.get(base + f"?target_id={ids['message']}").json()) == 1
    first = client.get(base + "?limit=1").json()
    second = client.get(base + "?limit=1&offset=1").json()
    assert first[0]["id"] != second[0]["id"]
    assert client.get(f"/api/workspaces/{ids['other']}/pins").json() == []
    assert client.get(base + "?limit=201").status_code == 422


@pytest.mark.parametrize(
    "fields",
    [
        {"title": " "},
        {"title": "x" * 201},
        {"notes": "x" * 4001},
        {"tags": ["x" * 81]},
        {"tags": [str(i) for i in range(31)]},
        {"user_id": "other"},
    ],
)
def test_metadata_bounds(pins_client, fields):
    client, _, ids, _ = pins_client
    assert create(client, ids, "thread", **fields).status_code == 422
    pin = create(client, ids, "thread").json()
    assert client.patch(f"/api/pins/{pin['id']}", json=fields).status_code == 422


@pytest.mark.parametrize("field", ["title", "notes", "tags"])
def test_patch_rejects_null(pins_client, field):
    client, _, ids, _ = pins_client
    pin = create(client, ids, "thread").json()
    assert client.patch(f"/api/pins/{pin['id']}", json={field: None}).status_code == 422


@pytest.mark.parametrize(
    "model,key,remaining",
    [
        (Message, "message", 2),
        (Artifact, "artifact", 2),
        (Thread, "thread", 0),
        (Workspace, "workspace", 0),
    ],
)
def test_target_deletion_cascades(pins_client, model, key, remaining):
    client, sessions, ids, _ = pins_client
    for kind in ("thread", "message", "artifact"):
        assert create(client, ids, kind).status_code == 201
    with sessions() as session:
        session.execute(delete(model).where(model.id == ids[key]))
        session.commit()
        assert len(list(session.scalars(select(Pin)))) == remaining


def test_unavailable_artifact_pin_and_live_message(pins_client):
    client, sessions, ids, _ = pins_client
    pin = create(client, ids, "artifact").json()
    answer_pin = create(client, ids, "message").json()
    with sessions() as session:
        session.get(Artifact, ids["artifact"]).durable = False
        session.get(Message, ids["message"]).content = "Updated answer"
        session.commit()
    assert client.get(f"/api/pins/{pin['id']}").status_code == 404
    messages = client.get(f"/api/pins/{answer_pin['id']}").json()["messages"]
    assert any(message["content"] == "Updated answer" for message in messages)
    assert client.delete(f"/api/pins/{pin['id']}").status_code == 204
