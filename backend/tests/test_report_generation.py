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
    _apply_restructure_plan,
    _apply_wording_patches,
    _compact_conversation,
    _compact_evidence,
    _messages,
    _preserve_embedded_blocks,
    _PromptVersion,
    _restore_asset_aliases,
    _validate_references,
)
from app.reports.schemas import ReportDocument
from app.workers.queue import Claim, LeaseLost


def test_compact_conversation_preserves_unanswered_selected_questions():
    turns = _compact_conversation(
        {
            "messages": [
                {
                    "id": "q1",
                    "run_id": "run1",
                    "role": "user",
                    "content": "First question?",
                },
                {
                    "id": "q2",
                    "run_id": "run2",
                    "role": "user",
                    "content": "Second question?",
                },
            ]
        },
        {},
    )
    assert [turn["question"] for turn in turns] == [
        "First question?",
        "Second question?",
    ]
    assert all(turn["final_answer"] == "" for turn in turns)


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
            {"id": "message-1", "role": "user", "content": f"See this {inline_uri}"},
            {
                "id": "message-2",
                "role": "assistant",
                "content": "The image is available as a retained artifact.",
            },
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
        [],
        [],
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
    assert "message-1" not in prompt
    assert inline_uri in snapshot["messages"][0]["content"]


def test_compact_conversation_keeps_every_selected_run_and_citation_link():
    snapshot = {
        "messages": [
            {"id": "u1", "role": "user", "run_id": "run-a", "content": "Question A?"},
            {
                "id": "a1",
                "role": "assistant",
                "run_id": "run-a",
                "content": "Answer A [e1].",
                "references": {
                    "evidence_ids": ["ev-a"],
                    "reference_aliases": {"e1": "ev-a"},
                },
            },
            {"id": "u2", "role": "user", "run_id": "run-b", "content": "Question B?"},
            {
                "id": "a2",
                "role": "assistant",
                "run_id": "run-b",
                "content": "Answer B [e2].",
                "references": {
                    "evidence_ids": ["ev-b"],
                    "reference_aliases": {"e2": "ev-b"},
                },
            },
            {"id": "u3", "role": "user", "run_id": "run-c", "content": "Question C?"},
            {
                "id": "a3",
                "role": "assistant",
                "run_id": "run-c",
                "content": "Answer C.",
                "references": {"evidence_ids": ["ev-c"]},
            },
        ]
    }
    turns = _compact_conversation(snapshot, {"ev-a": "e1", "ev-b": "e2", "ev-c": "e3"})
    assert [turn["question"] for turn in turns] == [
        "Question A?",
        "Question B?",
        "Question C?",
    ]
    assert [turn["evidence_refs"] for turn in turns] == [["e1"], ["e2"], ["e3"]]
    assert "run-a" not in json.dumps(turns)


def test_compact_evidence_deduplicates_location_and_maps_aliases():
    snapshot = {
        "messages": [
            {"role": "assistant", "references": {"evidence_ids": ["ev-a", "ev-b"]}}
        ],
        "sources": [
            {"id": "source-private", "display_name": "Policy.pdf", "current_version": 4}
        ],
        "evidence": [
            {
                "id": "ev-a",
                "kind": "document",
                "source_ids": ["source-private"],
                "details": {
                    "excerpt": "Same passage",
                    "location": {"page": 5},
                    "source_version": 4,
                },
            },
            {
                "id": "ev-b",
                "kind": "document",
                "source_ids": ["source-private"],
                "details": {
                    "excerpt": "Same passage",
                    "location": {"page": 5},
                    "source_version": 4,
                },
            },
        ],
    }
    sources, evidence, citations, aliases = _compact_evidence(snapshot)
    assert sources[0]["source_ref"] == "s1"
    assert len(evidence) == 1
    assert aliases == {"ev-a": "e1", "ev-b": "e1"}
    assert citations["e1"] == '[Policy.pdf; {"page": 5}]'
    assert "source-private" not in json.dumps([sources, evidence])


def test_compact_evidence_keeps_distinct_structured_records_without_excerpts():
    snapshot = {
        "messages": [
            {"role": "assistant", "references": {"evidence_ids": ["calc-a", "calc-b"]}}
        ],
        "sources": [{"id": "source-private", "display_name": "Results.csv"}],
        "evidence": [
            {
                "id": "calc-a",
                "kind": "calculation",
                "source_ids": ["source-private"],
                "details": {"result_artifact_id": "artifact-a", "location": {"row": 1}},
            },
            {
                "id": "calc-b",
                "kind": "calculation",
                "source_ids": ["source-private"],
                "details": {"result_artifact_id": "artifact-b", "location": {"row": 1}},
            },
        ],
    }
    _sources, evidence, _citations, aliases = _compact_evidence(snapshot)
    assert len(evidence) == 2
    assert aliases == {"calc-a": "e1", "calc-b": "e2"}


def test_wording_patches_allow_only_text_and_caption_fields():
    base = _document().model_dump(mode="json")
    patched = _apply_wording_patches(
        base,
        {
            "title": "Revised",
            "sections": [
                {
                    "section_id": "section-1",
                    "heading": "Overview",
                    "blocks": [
                        {"block_id": "paragraph-1", "text": "Clear wording."},
                        {"block_id": "figure-1", "caption": "Updated caption."},
                    ],
                }
            ],
        },
    )
    assert patched.sections[0].blocks[0].text == "Clear wording."
    assert patched.sections[0].blocks[1].artifact_id == "source-1"
    for invalid in (
        {"sections": [{"section_id": "missing", "heading": "Unknown"}]},
        {
            "sections": [
                {
                    "section_id": "section-1",
                    "blocks": [
                        {"block_id": "paragraph-1", "text": "A"},
                        {"block_id": "paragraph-1", "text": "B"},
                    ],
                }
            ]
        },
        {
            "sections": [
                {
                    "section_id": "section-1",
                    "blocks": [{"block_id": "figure-1", "text": "cannot edit refs"}],
                }
            ]
        },
        {
            "sections": [
                {
                    "section_id": "section-1",
                    "blocks": [
                        {
                            "block_id": "figure-1",
                            "caption": "x",
                            "artifact_id": "source-2",
                        }
                    ],
                }
            ]
        },
    ):
        with pytest.raises(ReportGenerationError):
            _apply_wording_patches(base, invalid)


def test_restructure_plan_rejects_unknown_duplicate_and_dropped_blocks():
    base = _document().model_dump(mode="json")
    valid_section = {
        "section_id": "section-1",
        "heading": "Summary",
        "block_ids": ["paragraph-1", "figure-1"],
    }
    for sections in (
        [{**valid_section, "block_ids": ["paragraph-1", "invented"]}],
        [{**valid_section, "block_ids": ["paragraph-1", "paragraph-1"]}],
        [{**valid_section, "block_ids": ["paragraph-1"]}],
    ):
        with pytest.raises(ReportGenerationError, match="preserve every block"):
            _apply_restructure_plan(base, {"sections": sections})


def test_asset_alias_is_restored_to_copied_identity():
    from app.reports.schemas import ReportDocument

    document = ReportDocument.model_validate(
        {
            "title": "Findings",
            "language": "en-IN",
            "sections": [
                {
                    "id": "s1",
                    "heading": "Chart",
                    "blocks": [
                        {
                            "id": "b1",
                            "type": "figure",
                            "artifact_id": "a1",
                            "caption": "Trend",
                        }
                    ],
                }
            ],
        }
    )
    restored = _restore_asset_aliases(
        document,
        {"a1": "private-artifact-id"},
        {"a1": {"media_type": "image/png", "display_name": "trend.png"}},
    )
    assert restored.sections[0].blocks[0].artifact_id == "private-artifact-id"


def test_prompt_marks_explicitly_selected_artifact_as_required():
    snapshot = {
        "messages": [],
        "assets": {},
        "selection": [
            {"kind": "artifact", "target_id": "private-artifact-id", "title": "Chart"}
        ],
    }
    messages = _messages(
        snapshot,
        _PromptVersion("Report", "en-IN", "initial", "", "workspace-1"),
        {"a1": {"asset_ref": "a1", "kind": "image"}},
        {"private-artifact-id": "a1"},
        [],
        [],
        {},
        {},
        None,
        Settings(),
    )
    payload = json.loads(messages[1]["content"])
    assert payload["required_artifact_refs"] == ["a1"]
    assert payload["artifacts"][0]["required"] is True
    assert payload["selection_context"][0]["artifact_ref"] == "a1"
    assert "private-artifact-id" not in messages[1]["content"]


def test_compact_prompt_has_smaller_serialized_context_than_frozen_snapshot():
    long_excerpt = "A representative policy passage. " * 60
    snapshot = {
        "title": "Policy findings",
        "instructions": "Summarize the selected questions.",
        "messages": [],
        "selection": [
            {"kind": "answer", "title": f"Answer {i}", "notes": ""} for i in range(3)
        ],
        "sources": [
            {
                "id": "private-source-id",
                "display_name": "Policy.pdf",
                "current_version": 7,
                "current_content_hash": "a" * 64,
            }
        ],
        "runs": [
            {
                "id": f"private-run-{i}",
                "source_versions": {"private-source-id": 7},
                "full_trace": "trace " * 100,
            }
            for i in range(3)
        ],
        "assets": {},
        "evidence": [],
    }
    for i in range(3):
        identity = f"private-evidence-{i}"
        snapshot["messages"].extend(
            [
                {
                    "id": f"private-user-{i}",
                    "run_id": f"private-run-{i}",
                    "role": "user",
                    "content": f"Question {i}?",
                },
                {
                    "id": f"private-answer-{i}",
                    "run_id": f"private-run-{i}",
                    "role": "assistant",
                    "content": f"Answer {i} [e{i + 1}].",
                    "references": {
                        "evidence_ids": [identity],
                        "reference_aliases": {f"e{i + 1}": identity},
                    },
                },
            ]
        )
        snapshot["evidence"].append(
            {
                "id": identity,
                "kind": "document",
                "source_ids": ["private-source-id"],
                "details": {
                    "excerpt": long_excerpt,
                    "location": {"page": i + 1},
                    "source_version": 7,
                    "trace": "trace " * 100,
                },
            }
        )
    sources, evidence, citations, aliases = _compact_evidence(snapshot)
    prompt = _messages(
        snapshot,
        _PromptVersion("Policy findings", "en-IN", "initial", "", "workspace-1"),
        {},
        {},
        sources,
        evidence,
        citations,
        aliases,
        None,
        Settings(),
    )
    before = len(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")))
    after = len(json.dumps(prompt, ensure_ascii=False, separators=(",", ":")))
    assert after < before
    assert "private-run-0" not in json.dumps(prompt)
    assert "trace" not in prompt[1]["content"]


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
                    {
                        "id": "m1",
                        "role": "user",
                        "content": "Question one?",
                        "run_id": "run-1",
                    },
                    {
                        "id": "m2",
                        "role": "assistant",
                        "content": "A finding [e1].",
                        "run_id": "run-1",
                        "references": {
                            "evidence_ids": ["evidence-1"],
                            "reference_aliases": {"e1": "evidence-1"},
                        },
                    },
                ],
                "assets": {},
                "selection": [],
                "sources": [{"id": "source-1", "display_name": "Frozen source"}],
                "runs": [{"id": "run-1", "source_versions": {"source-1": 3}}],
                "evidence": [
                    {
                        "id": "evidence-1",
                        "kind": "document",
                        "source_ids": ["source-1"],
                        "details": {
                            "excerpt": "A finding",
                            "location": {"page": 2},
                            "source_id": "source-1",
                            "source_version": 3,
                        },
                    }
                ],
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
    monkeypatch.setattr("app.reports.progress.factory", lambda: maker)
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
        assert "source-1" not in json.dumps(user_payload)
        assert "evidence-1" not in json.dumps(user_payload)
        assert "runs" not in user_payload
        assert user_payload["conversation"][0]["question"] == "Question one?"
        assert user_payload["conversation"][0]["final_answer"] == "A finding [e1]."
        assert user_payload["conversation"][0]["evidence_refs"] == ["e1"]
        assert user_payload["cited_evidence"][0]["location"] == {"page": 2}
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
