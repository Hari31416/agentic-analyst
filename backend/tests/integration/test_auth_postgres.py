"""Actual PostgreSQL bootstrap races, JWT account routes and migration rollback."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.auth.bootstrap import seed_admin
from app.config import Settings, get_settings
from app.db.models import User
from app.db.session import get_session
from app.main import app

pytestmark = [pytest.mark.auth, pytest.mark.integration]


@pytest.fixture
def postgres_auth(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = "test_auth_" + uuid4().hex
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(
            text(
                f'CREATE TABLE "{schema}".alembic_version (version_num varchar(32) PRIMARY KEY)'
            )
        )
    scoped = (
        make_url(url)
        .update_query_dict({"options": f"-csearch_path={schema},public"})
        .render_as_string(hide_password=False)
    )
    settings = Settings(
        _env_file=None,
        database_url=scoped,
        jwt_secret_key=SecretStr("integration-jwt-key-" + "x" * 40),
        admin_username="admin",
        admin_password=SecretStr("integration-password"),
    )
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    config = Config("alembic.ini")
    database = create_engine(scoped)
    sessions = sessionmaker(database, expire_on_commit=False)
    try:
        command.upgrade(config, "head")
        yield settings, sessions, config, database, schema
    finally:
        app.dependency_overrides.clear()
        database.dispose()
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def test_parallel_seed_and_lifespan_api(postgres_auth, monkeypatch):
    settings, sessions, _, _, _ = postgres_auth

    def seed(_):
        with sessions() as db:
            seed_admin(db, settings)

    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(seed, range(3)))
    with sessions() as db:
        assert len(list(db.scalars(select(User)))) == 1

    def dependency():
        with sessions() as db:
            yield db

    monkeypatch.setattr("app.main.get_settings", lambda: settings)
    monkeypatch.setattr("app.db.session.factory", lambda: sessions)
    app.dependency_overrides[get_session] = dependency
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "integration-password"},
        )
        assert response.status_code == 200
        headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        assert (
            client.post(
                "/api/auth/users",
                json={
                    "username": "regular",
                    "password": "regular-password",
                    "role": "user",
                },
                headers=headers,
            ).status_code
            == 201
        )
        assert (
            client.post(
                "/api/workspaces", json={"label": "shared"}, headers=headers
            ).status_code
            == 201
        )
        assert client.post("/api/auth/logout", headers=headers).status_code == 204
        assert client.get("/api/workspaces", headers=headers).status_code == 401


def test_auth_migration_roundtrip_preserves_workspaces(postgres_auth):
    _, _, config, database, schema = postgres_auth
    wid = str(uuid4())
    with database.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO workspaces (id,label,created_at) VALUES (:id,'preserved',now())"
            ),
            {"id": wid},
        )
    command.downgrade(config, "c2d14e6a901f")
    assert "users" not in inspect(database).get_table_names(schema=schema)
    command.upgrade(config, "head")
    assert {"users", "revoked_tokens"}.issubset(
        inspect(database).get_table_names(schema=schema)
    )
    with database.connect() as conn:
        assert (
            conn.scalar(text("SELECT label FROM workspaces WHERE id=:id"), {"id": wid})
            == "preserved"
        )
