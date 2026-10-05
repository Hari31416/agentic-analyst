"""Deliverable selection is separate from evidence references and file types."""

import json
from datetime import timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.exc import IntegrityError

from app.agent.references import ModelReferences
from app.api.artifacts import manifest
from app.contracts import ArtifactRole, FinalAnswer
from app.db.models import Artifact, Base, Run, Thread, Workspace
from app.evidence.validation import promote_outputs, validate_answer
from app.portability.service import export_workspace, import_workspace
from app.config import Settings
from app.storage.filesystem import FileStorage


@pytest.fixture
def artifacts_db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as session, session.begin():
        workspace = Workspace(label="roles")
        session.add(workspace)
        session.flush()
        thread = Thread(workspace_id=workspace.id, label="roles")
        session.add(thread)
        session.flush()
        run = Run(thread_id=thread.id, selected_source_ids=[], config={})
        session.add(run)
        session.flush()
        rows = []
        for role in ArtifactRole:
            row = Artifact(
                run_id=run.id,
                role=role.value,
                display_name=f"{role}.csv",
                media_type="text/csv",
                storage_key=f"derived/{role}.csv",
                sha256="0" * 64,
                byte_size=4,
                durable=True,
            )
            session.add(row)
            rows.append(row)
        session.flush()
        ids = {row.role: row.id for row in rows}
    yield sessions, workspace.id, run.id, ids
    engine.dispose()


def test_referenced_intermediate_is_not_promoted(artifacts_db):
    sessions, _, run_id, ids = artifacts_db
    with sessions() as session, session.begin():
        answer = FinalAnswer(text="Evidence", artifact_ids=list(ids.values()))
        validate_answer(session, session.get(Run, run_id), answer)
        promote_outputs(session, answer)
    with sessions() as session:
        assert session.get(Artifact, ids["intermediate"]).role == "intermediate"
        for role, identity in ids.items():
            assert manifest(session.get(Artifact, identity))["role"] == role


@pytest.mark.parametrize("role", ["intermediate", "output"])
def test_only_selected_deliverable_is_promoted(artifacts_db, role):
    sessions, _, run_id, ids = artifacts_db
    with sessions() as session, session.begin():
        answer = FinalAnswer(
            text="Result",
            artifact_ids=list(ids.values()),
            output_artifact_ids=[ids[role]],
        )
        validate_answer(session, session.get(Run, run_id), answer)
        promote_outputs(session, answer)
    with sessions() as session:
        assert session.get(Artifact, ids[role]).role == "output"
        assert session.get(Artifact, ids["metadata"]).role == "metadata"


@pytest.mark.parametrize("role", ["execution_code", "input_snapshot", "metadata"])
def test_supporting_artifacts_cannot_be_promoted(artifacts_db, role):
    sessions, _, run_id, ids = artifacts_db
    with sessions() as session:
        with pytest.raises(ValueError, match="cannot be"):
            validate_answer(
                session,
                session.get(Run, run_id),
                FinalAnswer(
                    text="Result",
                    artifact_ids=[ids[role]],
                    output_artifact_ids=[ids[role]],
                ),
            )


def test_deliverables_require_declared_accessible_references(artifacts_db):
    sessions, _, run_id, ids = artifacts_db
    with sessions() as session:
        run = session.get(Run, run_id)
        with pytest.raises(ValueError, match="also be declared"):
            validate_answer(
                session,
                run,
                FinalAnswer(text="Result", output_artifact_ids=[ids["intermediate"]]),
            )
        unknown = uuid4()
        with pytest.raises(ValueError, match="unknown artifact"):
            validate_answer(
                session,
                run,
                FinalAnswer(
                    text="Result", artifact_ids=[unknown], output_artifact_ids=[unknown]
                ),
            )
        run.thread_id = str(uuid4())
        # A different producer thread still fails the same reference boundary.
        other = Run(thread_id=str(uuid4()), selected_source_ids=[], config={})
        with pytest.raises(ValueError, match="another thread"):
            validate_answer(
                session,
                other,
                FinalAnswer(
                    text="Result",
                    artifact_ids=[ids["intermediate"]],
                    output_artifact_ids=[ids["intermediate"]],
                ),
            )


def test_deliverables_promotion_rolls_back_with_answer(artifacts_db):
    sessions, _, run_id, ids = artifacts_db
    with pytest.raises(RuntimeError):
        with sessions() as session, session.begin():
            answer = FinalAnswer(
                text="Result",
                artifact_ids=[ids["intermediate"]],
                output_artifact_ids=[ids["intermediate"]],
            )
            validate_answer(session, session.get(Run, run_id), answer)
            promote_outputs(session, answer)
            session.flush()
            raise RuntimeError("answer persistence failed")
    with sessions() as session:
        assert session.get(Artifact, ids["intermediate"]).role == "intermediate"


def test_output_short_references_resolve_like_evidence_references():
    references = ModelReferences()
    identity = str(uuid4())
    alias = references.reference("artifact", identity)
    answer = references.answer(
        json.dumps(
            {"text": "Result", "artifact_ids": [alias], "output_artifact_ids": [alias]}
        )
    )
    assert str(answer.output_artifact_ids[0]) == identity
    assert references.view(answer.model_dump(mode="json"))["output_artifact_ids"] == [
        alias
    ]


def test_roles_survive_workspace_roundtrip(artifacts_db, tmp_path, monkeypatch):
    sessions, workspace_id, _, ids = artifacts_db
    settings = Settings(_env_file=None, storage_root=tmp_path)
    storage = FileStorage(tmp_path)
    monkeypatch.setattr("app.portability.service.get_storage", lambda _: storage)
    with sessions() as session, session.begin():
        for row in session.scalars(select(Artifact)):
            stored = storage.put(row.storage_key, b"x\n1\n")
            row.sha256 = stored.sha256
            row.byte_size = stored.byte_size
    # SQLite drops timezone information; PostgreSQL preserves it in production.
    monkeypatch.setattr(
        "app.portability.service._iso",
        lambda value: value.replace(tzinfo=timezone.utc).isoformat() if value else None,
    )
    with sessions() as session:
        archive = export_workspace(session, workspace_id, settings)
    with sessions() as session, session.begin():
        result = import_workspace(session, archive, settings)
    with sessions() as session:
        for role, identity in ids.items():
            assert session.get(Artifact, result["id_map"][identity]).role == role


def test_database_rejects_unknown_roles(artifacts_db):
    sessions, _, _, ids = artifacts_db
    with pytest.raises(IntegrityError):
        with sessions() as session, session.begin():
            session.get(Artifact, ids["intermediate"]).role = "secretly_final"
