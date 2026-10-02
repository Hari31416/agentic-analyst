import os
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

import app.api.connections as connections_api
from app.config import Settings, get_settings
from app.db.models import Base, Connection, Dataset, Source, Workspace
from app.db.session import get_session
from app.sources.connections import decrypt_credentials

pytestmark = pytest.mark.integration


@pytest.fixture
def db_factory():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = "test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_connection_create_refresh_and_schema_are_redacted(db_factory, monkeypatch):
    key = Fernet.generate_key().decode()
    settings = Settings(database_encryption_key=key)
    with db_factory() as session, session.begin():
        workspace = Workspace(label="connector api")
        session.add(workspace)
        session.flush()
        workspace_id = workspace.id

    first_schema = [
        {
            "schema": "public",
            "name": "synthetic_applications",
            "identity": "public.synthetic_applications",
            "columns": [
                {"name": "application_id", "type": "VARCHAR(20)", "nullable": False}
            ],
            "relationships": [],
            "row_count": None,
            "row_count_estimated": True,
            "sample": [],
            "warnings": [],
        }
    ]
    second_schema = [
        {
            **first_schema[0],
            "columns": [
                *first_schema[0]["columns"],
                {
                    "name": "grant_amount_inr",
                    "type": "NUMERIC(12, 2)",
                    "nullable": False,
                },
            ],
        }
    ]
    monkeypatch.setattr(
        connections_api,
        "test_connection",
        lambda **_kwargs: {
            "ok": True,
            "status": "ready",
            "message": "Connection successful",
            "latency_ms": 1.0,
        },
    )
    monkeypatch.setattr(
        connections_api, "inspect_schema", lambda **_kwargs: first_schema
    )
    app = FastAPI()
    app.include_router(connections_api.router)

    def session_override():
        with db_factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: settings
    body = {
        "dialect": "postgresql",
        "host": "localhost",
        "port": 5432,
        "database_name": "analyst_eval",
        "username": "analyst",
        "password": "source-secret",
        "options": {"ssl_mode": "disable", "connect_timeout_seconds": 3},
        "display_name": "Evaluation database",
    }
    with TestClient(app) as client:
        tested = client.post(
            f"/api/workspaces/{workspace_id}/connections/test", json=body
        )
        assert tested.status_code == 200
        assert tested.json()["ok"] is True
        assert "source-secret" not in tested.text

        created = client.post(f"/api/workspaces/{workspace_id}/connections", json=body)
        assert created.status_code == 201, created.text
        payload = created.json()
        source_id = payload["source"]["id"]
        assert payload["source"]["kind"] == "postgresql"
        assert payload["datasets"][0]["identity"] == "public.synthetic_applications"
        assert "source-secret" not in created.text
        assert "encrypted_credentials" not in created.text

        with db_factory() as session:
            stored = session.scalar(
                select(Connection).where(Connection.source_id == source_id)
            )
            assert stored
            assert stored.encrypted_credentials != "source-secret"
            assert (
                decrypt_credentials(stored.encrypted_credentials, settings)
                == "source-secret"
            )

        schema_response = client.get(f"/api/sources/{source_id}/schema")
        assert schema_response.status_code == 200
        assert (
            schema_response.json()["datasets"][0]["identity"]
            == "public.synthetic_applications"
        )

        monkeypatch.setattr(
            connections_api, "inspect_schema", lambda **_kwargs: second_schema
        )
        refreshed = client.post(f"/api/sources/{source_id}/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["source"]["version"] == 2
        assert len(refreshed.json()["datasets"][0]["details"]["columns"]) == 2
    with db_factory() as session:
        source = session.get(Source, source_id)
        versions = session.scalars(
            select(Dataset.source_version)
            .where(Dataset.source_id == source_id)
            .order_by(Dataset.source_version)
        ).all()
        assert source and source.version == 2
        assert versions == [1, 2]
