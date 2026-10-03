from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.portability import router
from app.config import Settings, get_settings
from app.db.models import Base, Workspace
from app.db.session import get_session


def test_workspace_import_response_matches_outputs_view_contract(tmp_path):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def dependency():
        with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = dependency
    settings = Settings(_env_file=None, storage_root=tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with sessions() as session:
            workspace = Workspace(label="Portable workspace")
            session.add(workspace)
            session.commit()
            workspace_id = workspace.id

        with TestClient(app) as client:
            exported = client.get(f"/api/workspaces/{workspace_id}/export")
            assert exported.status_code == 200
            assert exported.headers["content-type"].startswith("application/zip")

            imported = client.post(
                "/api/portability/import",
                files={"file": ("workspace.zip", exported.content, "application/zip")},
            )
            assert imported.status_code == 201, imported.text
            result = imported.json()

        assert result["workspace"]["label"] == "Portable workspace"
        assert result["workspace"]["id"] != workspace_id
        assert result["id_map"][workspace_id] == result["workspace"]["id"]
        assert isinstance(result["reconnection_required"], list)
        assert isinstance(result["reindex_required"], list)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
