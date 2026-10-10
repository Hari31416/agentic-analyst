import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db.models import Base, Job, Report, ReportVersion, User, Workspace, now
from app.reports import generation
from app.reports.generation import (
    ReportGenerationError,
    _assert_lease,
    _messages,
    _preserve_embedded_blocks,
    _PromptVersion,
    _validate_references,
)
from app.reports.schemas import ReportDocument
from app.workers.queue import Claim, LeaseLost


def _document(*, heading="Summary", caption="Evidence", artifact_id="source-1"):
    return ReportDocument.model_validate(
        {
            "title": "Findings",
            "language": "en-IN",
            "sections": [
                {
                    "id": "section-1",
                    "heading": heading,
                    "blocks": [
                        {
                            "id": "paragraph-1",
                            "type": "paragraph",
                            "text": "A finding.",
                        },
                        {
                            "id": "figure-1",
                            "type": "figure",
                            "artifact_id": artifact_id,
                            "caption": caption,
                        },
                    ],
                }
            ],
        }
    )


def test_report_rejects_invented_artifact_reference():
    document = _document(artifact_id="invented")
    allowlist = {
        "source-1": {
            "media_type": "image/png",
            "display_name": "plot.png",
        }
    }

    with pytest.raises(ReportGenerationError, match="outside its copied sources"):
        _validate_references(document, allowlist)


def test_report_rejects_artifact_with_incompatible_type():
    document = _document()
    allowlist = {
        "source-1": {
            "media_type": "text/csv",
            "display_name": "data.csv",
        }
    }

    with pytest.raises(ReportGenerationError, match="wrong block type"):
        _validate_references(document, allowlist)


def test_wording_revision_preserves_structure_and_only_changes_caption():
    _preserve_embedded_blocks(
        _document(), _document(heading="Overview", caption="Updated wording"), "wording"
    )

    with pytest.raises(ReportGenerationError, match="preserve block IDs and order"):
        changed_id = _document().model_dump(mode="json")
        changed_id["sections"][0]["blocks"][1]["id"] = "figure-renamed"
        _preserve_embedded_blocks(
            _document(), ReportDocument.model_validate(changed_id), "wording"
        )

    with pytest.raises(ReportGenerationError, match="except captions"):
        changed_reference = _document().model_dump(mode="json")
        changed_reference["sections"][0]["blocks"][1]["artifact_id"] = "source-2"
        _preserve_embedded_blocks(
            _document(), ReportDocument.model_validate(changed_reference), "wording"
        )


def test_restructure_may_move_but_cannot_drop_embedded_artifacts():
    base = _document()
    moved = {
        "title": "Findings",
        "language": "en-IN",
        "sections": [
            {
                "id": "section-2",
                "heading": "Evidence",
                "blocks": [
                    {
                        "id": "figure-1",
                        "type": "figure",
                        "artifact_id": "source-1",
                        "caption": "Moved",
                    }
                ],
            },
            {
                "id": "section-1",
                "heading": "Summary",
                "blocks": [
                    {"id": "paragraph-1", "type": "paragraph", "text": "A finding."}
                ],
            },
        ],
    }
    _preserve_embedded_blocks(base, ReportDocument.model_validate(moved), "restructure")
    moved["sections"][0]["blocks"] = []
    with pytest.raises(ReportGenerationError, match="retain every embedded artifact"):
        _preserve_embedded_blocks(
            base, ReportDocument.model_validate(moved), "restructure"
        )


def test_report_checks_job_lease_before_mutation():
    task = Claim("job-1", "report_generation", "lease-1", {}, None, 1)
    session = SimpleNamespace(scalar=lambda _query: None)

    with pytest.raises(LeaseLost):
        _assert_lease(session, task)


def test_prompt_omits_inline_base64_without_changing_message_ids():
    inline_uri = "data:image/png;base64,AAECAwQFBgc="
    snapshot = {
        "messages": [
            {"id": "message-1", "role": "user", "content": f"See this {inline_uri}"}
        ],
        "assets": {},
    }
    messages = _messages(
        snapshot,
        _PromptVersion(
            title="Report",
            language="en-IN",
            mode="initial",
            feedback="",
            workspace_id="workspace-1",
        ),
        {},
        {},
        None,
        Settings(),
    )
    prompt = messages[1]["content"]
    prompt_data = json.loads(prompt)
    assert "data:image/" not in prompt
    assert "AAECAwQFBgc=" not in prompt
    assert "[inline image omitted; select its retained artifact]" in prompt
    assert prompt_data["messages"][0]["id"] == "message-1"
    assert inline_uri in snapshot["messages"][0]["content"]


def _report_job(monkeypatch, *, state="queued", lease_expiry=None):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, expire_on_commit=False)
    with maker.begin() as session:
        workspace = Workspace(id="workspace-1", label="Test")
        user = User(
            id="user-1",
            username="report-test",
            password_hash="unused",
            role="user",
        )
        session.add_all([workspace, user])
        report = Report(
            id="report-1",
            user_id=user.id,
            workspace_id=workspace.id,
            title="Current title",
        )
        session.add(report)
        version = ReportVersion(
            id="version-1",
            report_id=report.id,
            number=1,
            language="en-IN",
            state=state,
            snapshot={
                "title": "Frozen title",
                "messages": [
                    {"id": "m1", "role": "assistant", "content": "A finding."}
                ],
                "assets": {},
                "selection": [],
                "sources": [{"id": "source-1", "display_name": "Frozen source"}],
                "runs": [{"id": "run-1", "source_versions": {"source-1": 3}}],
                "evidence": [{"id": "evidence-1", "details": {"claim": "A finding"}}],
                "instructions": "Use concise language.",
            },
            mode="initial",
        )
        session.add(version)
        job = Job(
            id="job-1",
            kind="report_generation",
            workspace_id=workspace.id,
            dedupe_key="report:test",
            payload={"version_id": version.id},
            state="running",
            attempts=1,
            max_attempts=2,
            lease_token="lease-1",
            lease_owner="test-worker",
            lease_expires_at=lease_expiry or now() + timedelta(minutes=5),
        )
        session.add(job)
    monkeypatch.setattr(generation, "factory", lambda: maker)
    return (
        engine,
        maker,
        Claim(
            "job-1",
            "report_generation",
            "lease-1",
            {"version_id": "version-1"},
            None,
            1,
        ),
    )


def test_execute_report_uses_fake_model_and_persists_ready(monkeypatch):
    engine, maker, task = _report_job(monkeypatch)
    captured = {}

    class FakeModel:
        async def complete(self, messages, tools):
            captured["messages"] = messages
            captured["tools"] = tools
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "title": "Frozen title",
                        "language": "en-IN",
                        "sections": [
                            {
                                "id": "section-1",
                                "heading": "Summary",
                                "blocks": [
                                    {
                                        "id": "paragraph-1",
                                        "type": "paragraph",
                                        "text": "A finding.",
                                    }
                                ],
                            }
                        ],
                    }
                )
            )

    class FakeStorage:
        objects = {}

        def put(self, key, content):
            import hashlib

            self.objects[key] = content
            return SimpleNamespace(
                key=key,
                sha256=hashlib.sha256(content).hexdigest(),
                byte_size=len(content),
            )

        def delete(self, key):
            self.objects.pop(key, None)

    storage = FakeStorage()
    monkeypatch.setattr(generation, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        generation, "OpenAICompatibleModel", lambda _settings: FakeModel()
    )
    monkeypatch.setattr(generation, "get_storage", lambda _settings: storage)
    monkeypatch.setattr("app.reports.renderer.render_pdf", lambda *_: b"%PDF-test")

    try:
        result = asyncio.run(generation.execute_report(task, asyncio.Event()))
        assert result["state"] == "ready"
        assert captured["tools"] == []
        user_payload = json.loads(captured["messages"][1]["content"])
        assert user_payload["title"] == "Frozen title"
        assert user_payload["sources"][0]["id"] == "source-1"
        assert user_payload["evidence"][0]["id"] == "evidence-1"
        assert user_payload["runs"][0]["source_versions"]["source-1"] == 3
        with maker() as session:
            version = session.get(ReportVersion, "version-1")
            assert version.state == "ready"
            assert version.document["title"] == "Frozen title"
            assert version.pdf_key in storage.objects
            assert session.get(Job, "job-1").state == "running"
    finally:
        engine.dispose()


def test_execute_report_rejects_stale_lease_before_provider(monkeypatch):
    engine, maker, task = _report_job(
        monkeypatch, lease_expiry=now() - timedelta(minutes=1)
    )
    called = False

    class NeverModel:
        async def complete(self, *_args):
            nonlocal called
            called = True
            raise AssertionError("provider must not be called for stale lease")

    monkeypatch.setattr(generation, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        generation, "OpenAICompatibleModel", lambda _settings: NeverModel()
    )
    with pytest.raises(LeaseLost):
        asyncio.run(generation.execute_report(task, asyncio.Event()))
    assert not called
    with maker() as session:
        assert session.get(ReportVersion, "version-1").state == "queued"
    engine.dispose()


def test_execute_report_ready_version_is_idempotent(monkeypatch):
    engine, maker, task = _report_job(monkeypatch, state="ready")

    class NeverModel:
        async def complete(self, *_args):
            raise AssertionError("ready versions must not regenerate")

    monkeypatch.setattr(generation, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        generation, "OpenAICompatibleModel", lambda _settings: NeverModel()
    )
    assert asyncio.run(generation.execute_report(task, asyncio.Event()))[
        "already_ready"
    ]
    engine.dispose()


def test_execute_report_failure_can_be_marked_safely(monkeypatch):
    engine, maker, task = _report_job(monkeypatch)

    class InvalidModel:
        async def complete(self, *_args):
            return SimpleNamespace(content="not JSON")

    monkeypatch.setattr(generation, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        generation, "OpenAICompatibleModel", lambda _settings: InvalidModel()
    )
    monkeypatch.setattr(generation, "get_storage", lambda _settings: SimpleNamespace())
    with pytest.raises(ReportGenerationError) as error:
        asyncio.run(generation.execute_report(task, asyncio.Event()))
    with maker.begin() as session:
        generation.mark_report_failed(
            session, task, error.value.code, error.value.safe_message
        )
    with maker() as session:
        version = session.get(ReportVersion, "version-1")
        assert version.state == "failed"
        assert version.error == "report_output_invalid"
    engine.dispose()


def test_execute_report_maps_unsupported_glyph_to_safe_code(monkeypatch):
    engine, _maker, task = _report_job(monkeypatch)

    class FakeModel:
        async def complete(self, *_args):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "title": "Frozen title",
                        "language": "en-IN",
                        "sections": [
                            {
                                "id": "section-1",
                                "heading": "Summary",
                                "blocks": [
                                    {
                                        "id": "paragraph-1",
                                        "type": "paragraph",
                                        "text": "A finding.",
                                    }
                                ],
                            }
                        ],
                    }
                )
            )

    monkeypatch.setattr(generation, "get_settings", lambda: Settings())
    monkeypatch.setattr(
        generation, "OpenAICompatibleModel", lambda _settings: FakeModel()
    )
    monkeypatch.setattr(generation, "get_storage", lambda _settings: SimpleNamespace())

    def unsupported_glyph(*_args):
        raise ValueError("unsupported report glyph U+E000")

    monkeypatch.setattr("app.reports.renderer.render_pdf", unsupported_glyph)
    with pytest.raises(ReportGenerationError) as error:
        asyncio.run(generation.execute_report(task, asyncio.Event()))
    assert error.value.code == "report_glyph_unsupported"
    assert "U+E000" not in error.value.safe_message
    engine.dispose()
