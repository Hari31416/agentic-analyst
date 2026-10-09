"""Image archives must work with no access to the source storage."""

import io
import json
import zipfile
from datetime import timezone
from uuid import UUID

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import (
    Document,
    DocumentBlock,
    DocumentChunk,
    Evidence,
    Run,
    Source,
    Thread,
    Workspace,
)
from app.portability.service import export_workspace, import_workspace, PortabilityError
from app.storage.filesystem import FileStorage
from tests.test_documents import _database, _image_bytes


@pytest.fixture(autouse=True)
def sqlite_timestamps(monkeypatch):
    # SQLite drops timezone information; production PostgreSQL preserves UTC.
    from app.portability import service

    original = service._iso
    monkeypatch.setattr(
        service,
        "_iso",
        lambda value: (
            original(value.replace(tzinfo=timezone.utc))
            if value is not None and value.tzinfo is None
            else original(value)
        ),
    )


def _image_workspace(session, storage):
    workspace = Workspace(label="images")
    session.add(workspace)
    session.flush()
    stored = storage.put(f"originals/{workspace.id}/image.png", _image_bytes())
    source = Source(
        workspace_id=workspace.id,
        kind="document",
        display_name="image.png",
        state="ready",
        storage_key=stored.key,
        content_hash=stored.sha256,
        details={},
    )
    session.add(source)
    session.flush()
    document = Document(
        source_id=source.id,
        source_version=1,
        extractor_version="test",
        chunker_version="test",
        state="ready",
        stage="ready",
        progress=100,
        details={},
    )
    session.add(document)
    session.flush()
    image_key = f"derived/{workspace.id}/{document.id}/images/chart.png"
    storage.put(image_key, _image_bytes())
    block = DocumentBlock(
        document_id=document.id,
        ordinal=0,
        kind="image",
        text="chart",
        heading=None,
        location={"type": "image", "image_key": image_key},
        language="en-IN",
        scripts=[],
    )
    session.add(block)
    session.flush()
    chunk = DocumentChunk(
        document_id=document.id,
        ordinal=0,
        chunker_version="test",
        text="chart",
        normalized_text="chart",
        heading=None,
        location=dict(block.location),
        language="en-IN",
        block_ids=[block.id],
        token_count=1,
    )
    thread = Thread(workspace_id=workspace.id, label="citation")
    session.add_all([chunk, thread])
    session.flush()
    run = Run(
        thread_id=thread.id,
        state="completed",
        selected_source_ids=[source.id],
        config={},
    )
    session.add(run)
    session.flush()
    evidence = Evidence(
        run_id=run.id,
        kind="document",
        source_ids=[source.id],
        details={
            "image_key": image_key,
            "location": dict(block.location),
            "chunk_id": chunk.id,
        },
    )
    session.add(evidence)
    session.commit()
    return workspace, document, block, chunk, evidence


def test_images_roundtrip_into_fresh_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.portability.service.get_storage", lambda cfg: FileStorage(cfg.storage_root)
    )
    cfg = Settings(_env_file=None, storage_root=tmp_path / "source")
    storage = FileStorage(cfg.storage_root)
    sessions, session = _database()
    workspace, document, block, chunk, evidence = _image_workspace(session, storage)
    archive = export_workspace(session, workspace.id, cfg)
    destination = Settings(_env_file=None, storage_root=tmp_path / "destination")
    result = import_workspace(session, archive, destination)
    ids = result["id_map"]
    imported_block = session.get(DocumentBlock, ids[block.id])
    key = imported_block.location["image_key"]
    assert key != block.location["image_key"]
    assert FileStorage(destination.storage_root).read(key) == _image_bytes()
    assert session.get(DocumentChunk, ids[chunk.id]).location["image_key"] == key
    imported_evidence = session.get(Evidence, ids[evidence.id])
    assert imported_evidence.details["image_key"] == key
    assert imported_evidence.details["location"]["image_key"] == key
    assert imported_evidence.details["chunk_id"] == ids[chunk.id]
    assert session.get(DocumentChunk, ids[chunk.id]).block_ids == [ids[block.id]]

    # Both preview endpoints work after the source blobs disappear.
    storage.delete(block.location["image_key"])
    from app.api.documents import document_block_image
    from app.api.evidence import get_evidence_image

    monkeypatch.setattr("app.api.documents.get_settings", lambda: destination)
    monkeypatch.setattr(
        "app.api.documents.get_storage", lambda _: FileStorage(destination.storage_root)
    )
    monkeypatch.setattr("app.config.get_settings", lambda: destination)
    monkeypatch.setattr(
        "app.storage.factory.get_storage",
        lambda _: FileStorage(destination.storage_root),
    )
    assert (
        document_block_image(UUID(ids[document.id]), ids[block.id], session).media_type
        == "image/jpeg"
    )
    assert get_evidence_image(ids[evidence.id], session).media_type == "image/jpeg"
    session.close()


@pytest.mark.parametrize("missing_reference", ["asset", "chunk", "evidence"])
def test_import_rejects_missing_image_asset_before_creating_workspace(
    tmp_path, monkeypatch, missing_reference
):
    monkeypatch.setattr(
        "app.portability.service.get_storage", lambda cfg: FileStorage(cfg.storage_root)
    )
    cfg = Settings(_env_file=None, storage_root=tmp_path)
    sessions, session = _database()
    workspace, *_ = _image_workspace(session, FileStorage(tmp_path))
    archive = export_workspace(session, workspace.id, cfg)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        manifest = json.loads(source.read("manifest.json"))
        if missing_reference == "asset":
            manifest["assets"] = [
                asset for asset in manifest["assets"] if asset["purpose"] != "image"
            ]
        elif missing_reference == "chunk":
            manifest["entities"]["chunks"][0]["location"][
                "image_key"
            ] = "derived/missing.png"
        else:
            manifest["entities"]["evidence"][0]["details"][
                "image_key"
            ] = "derived/missing.png"
        needed = {asset["path"] for asset in manifest["assets"]}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("manifest.json", json.dumps(manifest))
            for path in needed:
                output.writestr(path, source.read(path))
    with pytest.raises(PortabilityError) as error:
        import_workspace(session, buffer.getvalue(), cfg)
    assert error.value.code == "image_asset_missing"
    assert list(session.scalars(select(Workspace.id))) == [workspace.id]
    session.close()
