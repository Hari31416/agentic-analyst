import hashlib
import io
import json
import os
import stat
import zipfile
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import (
    Artifact,
    Connection,
    Dataset,
    Document,
    DocumentBlock,
    DocumentChunk,
    Evidence,
    Job,
    Message,
    Run,
    Source,
    SummaryCache,
    Thread,
    Workspace,
)
from app.portability import PortabilityError, export_workspace, import_workspace
from app.storage.filesystem import FileStorage
from tests.integration.test_runtime import db_factory

pytestmark = pytest.mark.integration


def test_portable_workspace_roundtrip_preserves_content_and_remaps_evidence(
    db_factory, tmp_path, monkeypatch
):
    import app.portability.service as portability

    settings = Settings(_env_file=None, storage_root=tmp_path)
    storage = FileStorage(tmp_path)
    monkeypatch.setattr(portability, "get_storage", lambda _: storage)

    original = b"source original bytes"
    dataset_bytes = b"name,total\nA,10\n"
    artifact_bytes = b"generated report bytes"
    with db_factory() as session, session.begin():
        workspace = Workspace(label="portable")
        session.add(workspace)
        session.flush()
        source = Source(
            workspace_id=workspace.id,
            kind="docx",
            version=2,
            display_name="award.docx",
            state="ready",
            content_hash=hashlib.sha256(original).hexdigest(),
            storage_key=storage.put(
                f"originals/{workspace.id}/award.docx", original
            ).key,
            details={
                "media_type": "application/docx",
                "description": "Policy",
                "openai_api_key": "drop-me",
            },
        )
        db_source = Source(
            workspace_id=workspace.id,
            kind="postgresql",
            version=3,
            display_name="Finance DB",
            state="ready",
            details={"table_count": 1},
        )
        session.add_all([source, db_source])
        session.flush()
        connection = Connection(
            source_id=db_source.id,
            dialect="postgresql",
            host="db.example.test",
            port=5432,
            database_name="finance",
            username="reader",
            encrypted_credentials="encrypted-password-must-not-export",
            options={"ssl_mode": "require", "connect_timeout_seconds": 5},
        )
        session.add(connection)
        document = Document(
            source_id=source.id,
            source_version=2,
            extractor_version="document-extract-v2",
            chunker_version="structure-token-v3",
            state="ready",
            stage="ready",
            progress=100,
            details={
                "chunk_strategy": "structure",
                "warnings": [],
                "sandbox_auth_token": "drop-me",
            },
        )
        session.add(document)
        thread = Thread(workspace_id=workspace.id, label="Question history")
        session.add(thread)
        session.flush()
        run = Run(
            thread_id=thread.id,
            state="completed",
            selected_source_ids=[source.id, db_source.id],
            config={
                "sandbox_auth_token": "secret-config",
                "profile": "advanced",
                "retrieval": {"rerank": True},
            },
            outcome={
                "answer": "Applicants qualify.",
                "evidence_ids": [],
                "other_secret": "omit",
            },
        )
        session.add(run)
        session.flush()
        block = DocumentBlock(
            document_id=document.id,
            ordinal=0,
            kind="paragraph",
            text="Applicants qualify for an annual grant.",
            heading="Eligibility",
            location={"page": 1},
            language="en",
            scripts=["Latin"],
        )
        session.add(block)
        session.flush()
        chunk = DocumentChunk(
            document_id=document.id,
            ordinal=0,
            chunker_version=document.chunker_version,
            text=block.text,
            normalized_text=block.text.lower(),
            heading=block.heading,
            location=block.location,
            language="en",
            block_ids=[block.id],
            token_count=6,
        )
        dataset = Dataset(
            source_id=source.id,
            source_version=2,
            identity="derived.csv",
            schema_version="schema-v1",
            details={"columns": [{"name": "total", "type": "integer"}]},
            storage_key=storage.put(
                f"derived/{workspace.id}/dataset.csv", dataset_bytes
            ).key,
            designation="derived",
            lineage=[f"document_block:{block.id}"],
        )
        session.add_all([chunk, dataset])
        session.flush()
        evidence = Evidence(
            run_id=run.id,
            kind="document",
            source_ids=[source.id],
            details={
                "document_id": document.id,
                "chunk_id": chunk.id,
                "excerpt": chunk.text,
            },
        )
        session.add(evidence)
        session.flush()
        message = Message(
            thread_id=thread.id,
            run_id=run.id,
            role="assistant",
            content="Applicants qualify.",
            selected_source_ids=[source.id],
            references={
                "evidence_ids": [evidence.id],
                "citations": [{"chunk_id": chunk.id}],
            },
        )
        session.add(message)
        artifact_key = storage.put(
            f"derived/{workspace.id}/report.md", artifact_bytes
        ).key
        artifact = Artifact(
            run_id=run.id,
            storage_key=artifact_key,
            display_name="report.md",
            media_type="text/markdown",
            byte_size=len(artifact_bytes),
            sha256=hashlib.sha256(artifact_bytes).hexdigest(),
            lineage=[f"dataset:{dataset.id}"],
            durable=True,
        )
        session.add(artifact)
        session.add(
            Job(kind="agent_run", run_id=run.id, dedupe_key="no-replay", payload={})
        )
        summary = SummaryCache(
            fingerprint="a" * 64,
            scope="document",
            payload={
                "summary": "Applicants qualify for an annual grant.",
                "documents": [{"source_id": source.id}],
                "supporting_passages": [{"chunk_id": chunk.id}],
                "method": {"algorithm": "extractive-summary-v3"},
            },
        )
        session.add(summary)
        session.flush()
        workspace_id, source_id, db_source_id, document_id = (
            workspace.id,
            source.id,
            db_source.id,
            document.id,
        )
        block_id, chunk_id, dataset_id = block.id, chunk.id, dataset.id
        thread_id, run_id, evidence_id, message_id, artifact_id = (
            thread.id,
            run.id,
            evidence.id,
            message.id,
            artifact.id,
        )
        summary_id = summary.id

    with db_factory() as session:
        payload = export_workspace(session, workspace_id, settings)
    assert b"encrypted-password-must-not-export" not in payload
    assert b"secret-config" not in payload
    assert b"drop-me" not in payload

    with db_factory() as session:
        imported = import_workspace(session, payload, settings)
        ids = imported["id_map"]
        assert imported["workspace"]["id"] != workspace_id
        assert ids[workspace_id] == imported["workspace"]["id"]
        assert ids[source_id] != source_id
        assert imported["reindex_required"] == [ids[document_id]]
        assert imported["reconnection_required"][0]["source_id"] == ids[db_source_id]
        assert imported["reconnection_required"][0]["host"] == "db.example.test"

        copied_source = session.get(Source, ids[source_id])
        copied_db_source = session.get(Source, ids[db_source_id])
        assert copied_source and copied_source.details == {
            "media_type": "application/docx",
            "description": "Policy",
        }
        assert copied_db_source and copied_db_source.state == "disconnected"
        assert copied_db_source.details["portability"]["reconnection_required"] is True
        assert (
            session.scalar(
                select(func.count())
                .select_from(Connection)
                .where(Connection.source_id == ids[db_source_id])
            )
            == 0
        )
        assert storage.read(copied_source.storage_key) == original

        copied_document = session.get(Document, ids[document_id])
        copied_block = session.get(DocumentBlock, ids[block_id])
        copied_chunk = session.get(DocumentChunk, ids[chunk_id])
        copied_dataset = session.get(Dataset, ids[dataset_id])
        assert copied_document and copied_document.state == "ready"
        assert copied_document.index_generation_id is None
        assert copied_document.details["portability"]["reindex_required"] is True
        assert (
            copied_block
            and copied_block.text == "Applicants qualify for an annual grant."
        )
        assert copied_chunk and copied_chunk.block_ids == [ids[block_id]]
        assert copied_dataset and copied_dataset.lineage == [
            f"document_block:{ids[block_id]}"
        ]
        assert storage.read(copied_dataset.storage_key) == dataset_bytes

        copied_message = session.get(Message, ids[message_id])
        copied_evidence = session.get(Evidence, ids[evidence_id])
        copied_artifact = session.get(Artifact, ids[artifact_id])
        copied_run = session.get(Run, ids[run_id])
        assert copied_message and copied_message.thread_id == ids[thread_id]
        assert copied_message.references["evidence_ids"] == [ids[evidence_id]]
        assert copied_message.references["citations"][0]["chunk_id"] == ids[chunk_id]
        assert (
            copied_evidence
            and copied_evidence.details["document_id"] == ids[document_id]
        )
        assert copied_run and copied_run.state == "imported"
        assert copied_run.config == {"imported": True, "original_state": "completed"}
        assert (
            copied_artifact
            and storage.read(copied_artifact.storage_key) == artifact_bytes
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Job).where(Job.run_id == copied_run.id)
            )
            == 0
        )
        copied_summary = session.get(SummaryCache, ids[summary_id])
        assert copied_summary is not None


def _archive_with_entry(name: str, raw: bytes, *, symlink: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        info = zipfile.ZipInfo(name)
        if symlink:
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, raw)
    return output.getvalue()


def _manifest_archive(manifest: dict, assets: dict[str, bytes] | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest).encode())
        for name, raw in (assets or {}).items():
            archive.writestr(name, raw)
    return output.getvalue()


def _empty_manifest() -> dict:
    return {
        "format": "agentic-rag-analyst-workspace",
        "schema_version": 1,
        "workspace": {"id": str(uuid4()), "label": "empty"},
        "entities": {
            key: []
            for key in (
                "sources",
                "documents",
                "blocks",
                "chunks",
                "datasets",
                "threads",
                "runs",
                "messages",
                "evidence",
                "artifacts",
                "summaries",
            )
        },
        "assets": [],
    }


def test_portable_archive_rejects_expansion_bomb(db_factory, tmp_path):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("assets/" + "0" * 64, b"x" * (3 * 1024 * 1024))
    with db_factory() as session:
        with pytest.raises(PortabilityError, match="ratio"):
            import_workspace(
                session,
                output.getvalue(),
                Settings(_env_file=None, storage_root=tmp_path),
            )


def test_portable_archive_rejects_schema_mismatch(db_factory, tmp_path):
    manifest = _empty_manifest()
    manifest["schema_version"] = 99
    with db_factory() as session:
        with pytest.raises(PortabilityError, match="invalid or unsupported"):
            import_workspace(
                session,
                _manifest_archive(manifest),
                Settings(_env_file=None, storage_root=tmp_path),
            )


def test_portable_archive_rejects_hash_mismatch(db_factory, tmp_path):
    manifest = _empty_manifest()
    digest = hashlib.sha256(b"different").hexdigest()
    manifest["assets"] = [
        {
            "path": f"assets/{digest}",
            "owner_type": "source",
            "owner_id": str(uuid4()),
            "purpose": "original",
            "sha256": digest,
            "byte_size": len(b"different"),
        }
    ]
    with db_factory() as session:
        with pytest.raises(PortabilityError, match="hash or size"):
            import_workspace(
                session,
                _manifest_archive(manifest, {f"assets/{digest}": b"content"}),
                Settings(_env_file=None, storage_root=tmp_path),
            )


def test_portable_archive_rejects_invalid_uuid(db_factory, tmp_path):
    manifest = _empty_manifest()
    manifest["entities"]["sources"].append(
        {
            "id": "../bad",
            "kind": "pdf",
            "version": 1,
            "display_name": "bad.pdf",
            "state": "ready",
            "content_hash": None,
            "schema_version": None,
            "details": {},
            "connection": None,
            "original_available": False,
            "original_unavailable": False,
        }
    )
    with db_factory() as session:
        with pytest.raises(PortabilityError, match="UUID"):
            import_workspace(
                session,
                _manifest_archive(manifest),
                Settings(_env_file=None, storage_root=tmp_path),
            )


@pytest.mark.parametrize(
    "archive",
    [
        _archive_with_entry("../manifest.json", b"{}"),
        _archive_with_entry("assets/" + "0" * 64, b"x", symlink=True),
    ],
)
def test_portable_archive_rejects_traversal_and_symlink(db_factory, tmp_path, archive):
    settings = Settings(_env_file=None, storage_root=tmp_path)
    with db_factory() as session:
        with pytest.raises(PortabilityError):
            import_workspace(session, archive, settings)


def test_portable_archive_rejects_duplicate_zip_names(db_factory, tmp_path):
    output = io.BytesIO()
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("manifest.json", b"{}")
            archive.writestr("manifest.json", b"{}")
    settings = Settings(_env_file=None, storage_root=tmp_path)
    with db_factory() as session:
        with pytest.raises(PortabilityError, match="duplicate"):
            import_workspace(session, output.getvalue(), settings)
