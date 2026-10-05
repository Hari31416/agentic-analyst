"""Sidebar queries stay within the selected chat before the listing limit."""

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.artifacts import router
from app.db.models import Artifact, Run, Thread, Workspace
from app.db.session import get_session
from tests.test_artifact_roles import artifacts_db


def test_workspace_and_chat_artifact_scopes(artifacts_db):
    sessions, workspace_id, run_id, ids = artifacts_db
    with sessions() as session, session.begin():
        first_thread = session.get(Run, run_id).thread_id
        second_thread = Thread(workspace_id=workspace_id, label="Other chat")
        session.add(second_thread)
        session.flush()
        second_run = Run(thread_id=second_thread.id, config={})
        session.add(second_run)
        session.flush()
        # More than the list limit in the other chat must not crowd out this chat.
        session.add_all(
            [
                Artifact(
                    run_id=second_run.id,
                    storage_key=f"derived/{uuid4()}",
                    display_name="other.csv",
                    media_type="text/csv",
                    role="output",
                    sha256="0" * 64,
                    byte_size=4,
                )
                for _ in range(501)
            ]
        )
        other_workspace = Workspace(label="Other workspace")
        session.add(other_workspace)
        session.flush()
        foreign_thread = Thread(workspace_id=other_workspace.id, label="Foreign chat")
        session.add(foreign_thread)
        session.flush()
        second_id, foreign_id = second_thread.id, foreign_thread.id

    app = FastAPI()
    app.include_router(router)

    def db():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = db
    with TestClient(app) as client:
        path = f"/api/workspaces/{workspace_id}/artifacts"
        all_rows = client.get(path)
        assert all_rows.status_code == 200
        assert len(all_rows.json()) == 500
        own_rows = client.get(path, params={"thread_id": first_thread})
        assert own_rows.status_code == 200
        assert {row["id"] for row in own_rows.json()} == set(ids.values())
        assert {row["run_id"] for row in own_rows.json()} == {run_id}
        second_rows = client.get(path, params={"thread_id": second_id})
        assert len(second_rows.json()) == 500
        assert set(ids.values()).isdisjoint({row["id"] for row in second_rows.json()})
        for thread_id in (foreign_id, str(uuid4())):
            assert client.get(path, params={"thread_id": thread_id}).status_code == 404
        assert client.get(path, params={"thread_id": "bad-id"}).status_code == 422
