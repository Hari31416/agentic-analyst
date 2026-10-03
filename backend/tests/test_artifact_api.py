import json

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.api.artifacts as artifacts_api
from app.config import Settings
from app.db.models import Artifact, Base, Run, Thread, Workspace
from app.db.session import get_session
from app.main import app
from app.storage.filesystem import FileStorage


def test_artifact_manifest_pagination_chart_and_exports(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)

    def dependency():
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = dependency
    settings = Settings(_env_file=None, storage_root=tmp_path)
    storage = FileStorage(tmp_path)
    monkeypatch.setattr(artifacts_api, "get_settings", lambda: settings)
    monkeypatch.setattr(artifacts_api, "get_storage", lambda _settings: storage)
    with sessions() as session, session.begin():
        workspace = Workspace(label="artifact test")
        session.add(workspace)
        session.flush()
        thread = Thread(workspace_id=workspace.id, label="report")
        session.add(thread)
        session.flush()
        run = Run(thread_id=thread.id, state="completed")
        session.add(run)
        session.flush()
        csv_file = storage.put(
            f"derived/{run.id}/table.csv",
            b"district,total\nA,12345678901234567890\nB,=1+1\n",
        )
        table = Artifact(
            run_id=run.id,
            storage_key=csv_file.key,
            display_name="result.csv",
            media_type="text/csv",
            byte_size=csv_file.byte_size,
            sha256=csv_file.sha256,
            lineage=["dataset:one"],
        )
        chart_body = json.dumps(
            {
                "schema_version": 1,
                "data": [{"type": "bar", "x": ["A"], "y": [1.5]}],
                "layout": {"title": "Total"},
                "config": {"responsive": True, "displayModeBar": False},
            }
        ).encode()
        chart_file = storage.put(f"derived/{run.id}/chart.json", chart_body)
        chart = Artifact(
            run_id=run.id,
            storage_key=chart_file.key,
            display_name="chart.json",
            media_type="application/json",
            byte_size=chart_file.byte_size,
            sha256=chart_file.sha256,
        )
        session.add_all([table, chart])
        session.flush()
        workspace_id, table_id, chart_id = workspace.id, table.id, chart.id
    try:
        client = TestClient(app)
        listing = client.get(f"/api/workspaces/{workspace_id}/artifacts")
        assert listing.status_code == 200
        assert {row["id"] for row in listing.json()} == {table_id, chart_id}
        detail = client.get(f"/api/artifacts/{table_id}").json()
        assert detail["lineage"] == ["dataset:one"]
        page = client.get(f"/api/artifacts/{table_id}/rows?offset=1&limit=1").json()
        assert page["rows"] == [{"district": "B", "total": "=1+1"}]
        assert page["total_rows"] == 2
        chart_response = client.get(f"/api/artifacts/{chart_id}/chart")
        assert chart_response.status_code == 200
        assert chart_response.json()["data"][0]["y"] == [1.5]
        csv_response = client.get(f"/api/artifacts/{table_id}/download?format=csv")
        assert b"' =1+1" not in csv_response.content
        assert b"'=1+1" in csv_response.content
        xlsx_response = client.get(f"/api/artifacts/{table_id}/download?format=xlsx")
        assert xlsx_response.status_code == 200
        parquet_response = client.get(
            f"/api/artifacts/{table_id}/download?format=parquet"
        )
        assert parquet_response.status_code == 200
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
