import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import login_limiter
from app.auth.bootstrap import seed_admin
from app.auth.security import (
    AUDIENCE,
    COOKIE,
    ISSUER,
    create_token,
    hash_password,
    verify_password,
)
from app.config import Settings, get_settings
from app.db.models import Base, User
from app.db.session import get_session
from app.main import app

pytestmark = pytest.mark.auth


@pytest.fixture
def auth_db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        jwt_secret_key=SecretStr("a" * 40),
        admin_username="Admin",
        admin_password=SecretStr("first-password"),
    )
    with sessions() as db:
        seed_admin(db, settings)

    def dependency():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_session] = dependency
    app.dependency_overrides[get_settings] = lambda: settings
    login_limiter.entries.clear()
    try:
        yield TestClient(app), sessions, settings
    finally:
        app.dependency_overrides.clear()
        login_limiter.entries.clear()
        engine.dispose()


def signin(client, username="admin", password="first-password"):
    response = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def bearer(token):
    return {"Authorization": "Bearer " + token}


def test_seed_is_idempotent_and_does_not_reset(auth_db):
    _, sessions, settings = auth_db
    with sessions() as db:
        admin = db.scalar(select(User))
        assert admin.username == "admin" and admin.role == "admin"
        assert admin.password_hash != "first-password"
        original = admin.password_hash
        settings.admin_password = SecretStr("new-env-password")
        seed_admin(db, settings)
        assert db.scalar(select(User)).password_hash == original
        assert len(list(db.scalars(select(User)))) == 1
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as db:
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            seed_admin(db, Settings(_env_file=None))
        with pytest.raises(RuntimeError, match="ADMIN_USERNAME"):
            seed_admin(db, Settings(_env_file=None, jwt_secret_key=SecretStr("a" * 40)))
    engine.dispose()


def test_login_cookie_and_bearer_and_logout(auth_db):
    client, _, _ = auth_db
    result = client.post(
        "/api/auth/login", json={"username": "ADMIN", "password": "first-password"}
    )
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    cookie = result.headers["set-cookie"]
    assert (
        "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api" in cookie
    )
    assert "password" not in result.json()["user"]
    token = result.json()["access_token"]
    assert client.get("/api/auth/me").json()["role"] == "admin"
    assert client.post("/api/workspaces", json={"label": "blocked"}).status_code == 403
    origin = {"Origin": "http://localhost:5173"}
    assert (
        client.post(
            "/api/workspaces", json={"label": "shared"}, headers=origin
        ).status_code
        == 201
    )
    assert client.post("/api/auth/logout", headers=origin).status_code == 204
    assert COOKIE not in client.cookies
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401
    fresh = signin(client)
    assert client.get("/api/auth/me", headers=bearer(fresh)).status_code == 200


def test_all_business_routes_deny_anonymous(auth_db):
    client, _, _ = auth_db
    assert client.get("/api/health").status_code == 200
    from fastapi.routing import APIRoute

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods:
            if (method, route.path) in {
                ("GET", "/api/health"),
                ("POST", "/api/auth/login"),
            }:
                continue
            path = route.path
            import re

            path = re.sub(r"\{[^}]+\}", str(uuid4()), path)
            response = client.request(method, path)
            assert response.status_code == 401, (method, path, response.text)


def test_roles_crud_and_immediate_token_invalidation(auth_db):
    client, _, _ = auth_db
    admin_token = signin(client)
    headers = bearer(admin_token)
    user_response = client.post(
        "/api/auth/users",
        json={"username": "reader", "password": "reader-password", "role": "user"},
        headers=headers,
    )
    assert user_response.status_code == 201
    user_id = user_response.json()["id"]
    assert "password_hash" not in user_response.text
    assert (
        client.post(
            "/api/auth/users",
            json={"username": "READER", "password": "reader-password"},
            headers=headers,
        ).status_code
        == 409
    )
    user_token = signin(client, "reader", "reader-password")
    for method, path, body in [
        ("GET", "/api/auth/users", None),
        (
            "POST",
            "/api/auth/users",
            {"username": "bad-admin", "password": "password123", "role": "admin"},
        ),
        ("PATCH", f"/api/auth/users/{user_id}", {"role": "admin"}),
    ]:
        assert (
            client.request(
                method, path, json=body, headers=bearer(user_token)
            ).status_code
            == 403
        )
    assert client.get("/api/workspaces", headers=bearer(user_token)).status_code == 200
    assert (
        client.patch(
            f"/api/auth/users/{user_id}", json={"role": "admin"}, headers=headers
        ).status_code
        == 200
    )
    assert client.get("/api/auth/me", headers=bearer(user_token)).status_code == 401
    new_user_token = signin(client, "reader", "reader-password")
    assert (
        client.get("/api/auth/users", headers=bearer(new_user_token)).status_code == 200
    )
    assert (
        client.patch(
            f"/api/auth/users/{user_id}", json={"is_active": False}, headers=headers
        ).status_code
        == 200
    )
    assert client.get("/api/auth/me", headers=bearer(new_user_token)).status_code == 401
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "reader", "password": "reader-password"},
        ).status_code
        == 401
    )
    assert (
        client.patch(
            f"/api/auth/users/{user_id}",
            json={
                "password": "replacement-password",
                "is_active": True,
                "role": "user",
            },
            headers=headers,
        ).status_code
        == 200
    )
    signin(client, "reader", "replacement-password")


def test_last_admin_and_validation_secret_handling(auth_db):
    client, _, _ = auth_db
    token = signin(client)
    user_id = client.get("/api/auth/me").json()["id"]
    for body in [{"role": "user"}, {"is_active": False}]:
        assert (
            client.patch(
                f"/api/auth/users/{user_id}", json=body, headers=bearer(token)
            ).status_code
            == 409
        )
    for body in [
        {"role": "superuser"},
        {"password": "short"},
        {"password": None},
        {"is_active": None},
    ]:
        response = client.patch(
            f"/api/auth/users/{user_id}", json=body, headers=bearer(token)
        )
        assert response.status_code == 422
        assert all("input" not in error for error in response.json()["detail"])
        assert "superuser" not in response.text
    assert (
        client.get(
            "/api/auth/me", headers={"Authorization": "Basic invalid"}
        ).status_code
        == 401
    )


@pytest.mark.parametrize(
    "kind",
    [
        "expired",
        "wrong-key",
        "wrong-audience",
        "wrong-issuer",
        "wrong-type",
        "missing-exp",
        "deleted",
        "version",
        "unsigned",
    ],
)
def test_jwt_rejections(auth_db, kind):
    client, sessions, settings = auth_db
    with sessions() as db:
        user = db.scalar(select(User))
        token = create_token(user, settings)
        payload = jwt.decode(token, options={"verify_signature": False})
        secret = settings.jwt_secret_key.get_secret_value()
        if kind == "expired":
            payload["exp"] = int(time.time()) - 1
        elif kind == "wrong-key":
            secret = "b" * 40
        elif kind == "wrong-audience":
            payload["aud"] = "foreign"
        elif kind == "wrong-issuer":
            payload["iss"] = "foreign"
        elif kind == "wrong-type":
            payload["type"] = "refresh"
        elif kind == "missing-exp":
            del payload["exp"]
        elif kind == "deleted":
            payload["sub"] = str(uuid4())
        elif kind == "version":
            payload["ver"] += 1
        token = jwt.encode(
            payload,
            "" if kind == "unsigned" else secret,
            algorithm="none" if kind == "unsigned" else "HS256",
        )
    assert client.get("/api/auth/me", headers=bearer(token)).status_code == 401


def test_csrf_and_limiter(auth_db):
    client, _, settings = auth_db
    signin(client)
    for headers in [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://localhost:5173", "Sec-Fetch-Site": "cross-site"},
    ]:
        assert (
            client.post(
                "/api/workspaces", json={"label": "no"}, headers=headers
            ).status_code
            == 403
        )
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "first-password"},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    login_limiter.entries.clear()
    for _ in range(10):
        response = client.post(
            "/api/auth/login", json={"username": "missing", "password": "bad"}
        )
        assert response.status_code == 401
    assert (
        client.post(
            "/api/auth/login", json={"username": "admin", "password": "first-password"}
        ).status_code
        == 429
    )
    assert verify_password("wrong", hash_password("correct")) is False


def test_stream_rechecks_tokens_and_cookie_downloads(auth_db, monkeypatch, tmp_path):
    from app.auth.security import decode_token, stream_authorized
    from app.db.models import Artifact, Run, Thread, Workspace
    from app.db.repository import append_event
    from app.storage.filesystem import FileStorage

    client, sessions, settings = auth_db
    monkeypatch.setattr("app.db.session.factory", lambda: sessions)
    monkeypatch.setattr("app.api.chat.factory", lambda: sessions)
    monkeypatch.setattr("app.api.artifacts.get_settings", lambda: settings)
    settings.storage_root = tmp_path
    monkeypatch.setattr("app.api.chat.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.chat.get_storage", lambda: FileStorage(tmp_path))
    token = signin(client)
    claims = decode_token(token, settings)
    assert stream_authorized(claims)
    with sessions() as db:
        workspace = Workspace(label="shared")
        db.add(workspace)
        db.flush()
        thread = Thread(workspace_id=workspace.id, label="stream")
        db.add(thread)
        db.flush()
        run = Run(thread_id=thread.id, state="completed")
        db.add(run)
        db.flush()
        append_event(db, run.id, "completed", {"text": "safe"})
        stored = FileStorage(tmp_path).put(f"derived/{run.id}/sample.txt", b"sample")
        artifact = Artifact(
            run_id=run.id,
            display_name="sample.txt",
            media_type="text/plain",
            storage_key=stored.key,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            durable=True,
        )
        db.add(artifact)
        db.commit()
        run_id, artifact_id = run.id, artifact.id
    response = client.get(f"/api/runs/{run_id}/events")
    assert response.status_code == 200 and '"completed"' in response.text
    assert client.get(f"/api/artifacts/{artifact_id}/content").content == b"sample"
    expired = {**claims, "exp": 0}
    assert not stream_authorized(expired)
    with sessions() as db:
        user = db.get(User, claims["sub"])
        user.token_version += 1
        db.commit()
    assert not stream_authorized(claims)
    assert client.get(f"/api/runs/{run_id}/events").status_code == 401
    assert client.get(f"/api/artifacts/{artifact_id}/content").status_code == 401
