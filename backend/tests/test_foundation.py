import hashlib
import json
import logging
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError, BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.audit.redaction import SafeJsonFormatter, redact
from app.config import Settings
from app.contracts import CreateLabel, ToolResult
from app.db.models import Base, Event, Run, Thread, Workspace
from app.db.repository import append_event, audit
from app.db.session import get_session
from app.language.metadata import LanguageMetadata
from app.main import app
from app.storage.filesystem import FileStorage


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with sessionmaker(engine, expire_on_commit=False)() as db:
        yield db
    engine.dispose()


def test_workspace_and_threads_persist(session):
    app.dependency_overrides[get_session] = lambda: session
    try:
        client = TestClient(app)
        workspace = client.post("/api/workspaces", json={"label": "Pilot"}).json()
        assert (
            client.post(
                f"/api/workspaces/{workspace['id']}/threads",
                json={"label": "Eligibility"},
            ).status_code
            == 201
        )
        assert len(client.get(f"/api/workspaces/{workspace['id']}/threads").json()) == 1
        assert client.get("/api/workspaces/missing/threads").status_code == 404
        assert client.post("/api/workspaces", json={"label": "  "}).status_code == 422
        assert client.get("/api/health").json() == {"status": "ok"}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "key",
    [
        "../secret",
        "/originals/x",
        "derived/../x",
        "derived//x",
        "originals/a\\b",
        "derived/./x",
        "derived/",
    ],
)
def test_storage_rejects_traversal(tmp_path, key):
    with pytest.raises(ValueError):
        FileStorage(tmp_path).put(key, b"no")


def test_storage_immutable_atomic_and_hashed(tmp_path):
    storage = FileStorage(tmp_path)
    original = storage.put("originals/source/data.csv", b"original")
    assert original.sha256 == hashlib.sha256(b"original").hexdigest()
    assert storage.put(original.key, b"original") == original
    with pytest.raises(ValueError):
        storage.put(original.key, b"overwrite")
    with patch("app.storage.filesystem.os.link", side_effect=OSError("disk failed")):
        with pytest.raises(OSError):
            storage.put("derived/run/report.csv", b"report")
    assert not storage.path("derived/run/report.csv").exists()
    assert not list(tmp_path.rglob(".pending-*"))
    assert storage.verify(original.key, original.sha256, original.byte_size)
    with pytest.raises(ValueError):
        storage.read(original.key, max_bytes=3)


def test_storage_rejects_symlinks(tmp_path):
    storage = FileStorage(tmp_path)
    (tmp_path / "derived").symlink_to(tmp_path.parent)
    with pytest.raises(ValueError):
        storage.put("derived/out.csv", b"bad")


def test_canonical_unknown_and_mixed_language():
    assert LanguageMetadata().tags == []
    assert LanguageMetadata(tags=["hi", "en"]).tags == ["hi-IN", "en-IN"]
    assert LanguageMetadata(tags=["hi", "en"]).mixed
    with pytest.raises(ValidationError):
        LanguageMetadata(tags=["mixed"])


def test_config_does_not_require_model_secrets():
    settings = Settings(_env_file=None)
    assert not settings.model_configured
    with pytest.raises(ValidationError):
        Settings(_env_file=None, sandbox_base_url="http://user:secret@host")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_tool_calls=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_encryption_key="bad")


def test_redaction():
    secret = {
        "nested": {"password": "unsafe", "authorization": "Bearer unsafe"},
        "url": "postgresql://user:unsafe@localhost",
    }
    assert "unsafe" not in json.dumps(redact(secret))
    record = logging.LogRecord(
        "app", logging.INFO, "file", 1, "postgresql://user:unsafe@host", (), None
    )
    assert "unsafe" not in SafeJsonFormatter().format(record)


def test_events_and_audit_have_stable_references(session):
    workspace = Workspace(label="one")
    session.add(workspace)
    session.flush()
    thread = Thread(workspace_id=workspace.id, label="one")
    session.add(thread)
    session.flush()
    run = Run(thread_id=thread.id)
    session.add(run)
    session.flush()
    events = [
        append_event(session, run.id, "started", {}),
        append_event(session, run.id, "done", {}),
    ]
    assert [event.sequence for event in events] == [1, 2]
    row = audit(
        session,
        action="tool",
        decision="allowed",
        reason_code="valid",
        run_id=run.id,
        details={"api_key": "unsafe"},
    )
    session.commit()
    assert session.get(Event, events[1].id).run_id == run.id
    assert row.details["api_key"] == "[redacted]"


def test_contracts_reject_unknown_fields_and_preserve_decimals():
    with pytest.raises(ValidationError):
        CreateLabel(label="valid", unknown=True)

    class Money(BaseModel):
        amount: Decimal

    value = Money(amount=Decimal("25000.01"))
    assert json.loads(value.model_dump_json())["amount"] == "25000.01"
    assert ToolResult(status="failed", summary="safe").artifact_ids == []
