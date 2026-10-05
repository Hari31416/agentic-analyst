from __future__ import annotations

from app.audit.redaction import contains_secret

import hashlib
import io
import json
import re
import stat
import zipfile
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    Connection,
    Dataset,
    Document,
    DocumentBlock,
    DocumentChunk,
    Evidence,
    Message,
    Run,
    Source,
    SummaryCache,
    Thread,
    Workspace,
)
from app.portability.contracts import PortableManifest
from app.storage.factory import get_storage

FORMAT = "agentic-rag-analyst-workspace"
SCHEMA_VERSION = 1
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 20_000
MAX_ASSETS = 10_000
MAX_COMPRESSION_RATIO = 200
SUPPORTED_SUMMARY_ALGORITHMS = {"extractive-summary-v3"}
ENTITY_KEYS = {
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
}


class PortabilityError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def export_workspace(session: Session, workspace_id: str, settings: Any) -> bytes:
    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        raise PortabilityError("workspace_not_found", "Workspace not found.", 404)

    sources = list(
        session.scalars(
            select(Source)
            .where(Source.workspace_id == workspace_id)
            .order_by(Source.id)
        )
    )
    source_ids = [row.id for row in sources]
    documents = (
        _many(session, Document, Document.source_id.in_(source_ids))
        if source_ids
        else []
    )
    document_ids = [row.id for row in documents]
    datasets = (
        _many(session, Dataset, Dataset.source_id.in_(source_ids)) if source_ids else []
    )
    threads = _many(session, Thread, Thread.workspace_id == workspace_id)
    thread_ids = [row.id for row in threads]
    runs = _many(session, Run, Run.thread_id.in_(thread_ids)) if thread_ids else []
    run_ids = [row.id for row in runs]
    messages = (
        _many(session, Message, Message.thread_id.in_(thread_ids)) if thread_ids else []
    )
    evidence = _many(session, Evidence, Evidence.run_id.in_(run_ids)) if run_ids else []
    artifacts = (
        _many(session, Artifact, Artifact.run_id.in_(run_ids)) if run_ids else []
    )
    connections = (
        _many(session, Connection, Connection.source_id.in_(source_ids))
        if source_ids
        else []
    )
    connection_by_source = {row.source_id: row for row in connections}
    blocks = (
        _many(session, DocumentBlock, DocumentBlock.document_id.in_(document_ids))
        if document_ids
        else []
    )
    chunks = (
        _many(session, DocumentChunk, DocumentChunk.document_id.in_(document_ids))
        if document_ids
        else []
    )
    summaries = _portable_summaries(session, source_ids)

    entities: dict[str, list[dict[str, Any]]] = {
        "sources": [
            _source_record(row, connection_by_source.get(row.id)) for row in sources
        ],
        "documents": [_document_record(row) for row in documents],
        "blocks": [_block_record(row) for row in blocks],
        "chunks": [_chunk_record(row) for row in chunks],
        "datasets": [_dataset_record(row) for row in datasets],
        "threads": [_thread_record(row) for row in threads],
        "runs": [_run_record(row) for row in runs],
        "messages": [_message_record(row) for row in messages],
        "evidence": [_evidence_record(row) for row in evidence],
        "artifacts": [_artifact_record(row) for row in artifacts],
        "summaries": summaries,
    }

    storage = get_storage(settings)
    asset_bytes: dict[str, bytes] = {}
    assets: list[dict[str, Any]] = []

    def add_asset(
        owner_type: str,
        owner_id: str,
        purpose: str,
        key: str | None,
        expected_hash: str | None = None,
        expected_size: int | None = None,
    ) -> None:
        if key is None:
            return
        try:
            raw = storage.read(key, MAX_MEMBER_BYTES)
            if contains_secret(raw):
                raise PortabilityError(
                    "configured_secret_asset",
                    "Archive asset contains configured credentials",
                )
        except PortabilityError:
            raise
        except Exception as exc:
            raise PortabilityError(
                "asset_unavailable", f"A stored {purpose} asset could not be read."
            ) from exc
        digest = hashlib.sha256(raw).hexdigest()
        if (expected_hash and digest != expected_hash) or (
            expected_size is not None and len(raw) != expected_size
        ):
            raise PortabilityError(
                "asset_hash_mismatch", "A stored asset failed verification."
            )
        if len(raw) > MAX_MEMBER_BYTES:
            raise PortabilityError(
                "asset_too_large", "A stored asset exceeds the export limit."
            )
        path = f"assets/{digest}"
        previous = asset_bytes.setdefault(path, raw)
        if previous != raw:
            raise PortabilityError(
                "asset_hash_collision", "Stored assets failed integrity validation."
            )
        assets.append(
            {
                "path": path,
                "owner_type": owner_type,
                "owner_id": owner_id,
                "purpose": purpose,
                "sha256": digest,
                "byte_size": len(raw),
            }
        )

    for source in sources:
        if source.kind not in {"mysql", "postgresql"}:
            add_asset(
                "source", source.id, "original", source.storage_key, source.content_hash
            )
    for dataset in datasets:
        add_asset("dataset", dataset.id, "dataset", dataset.storage_key)
    for artifact in artifacts:
        if artifact.durable:
            add_asset(
                "artifact",
                artifact.id,
                "artifact",
                artifact.storage_key,
                artifact.sha256,
                artifact.byte_size,
            )

    manifest = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "workspace": {"id": workspace.id, "label": workspace.label},
        "entities": entities,
        "assets": assets,
    }
    if contains_secret(manifest):
        raise PortabilityError(
            "configured_secret_metadata",
            "Archive metadata contains configured credentials",
        )
    manifest_bytes = _json_bytes(manifest)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise PortabilityError(
            "manifest_too_large", "Workspace metadata exceeds the export limit."
        )
    output = io.BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        for path, raw in sorted(asset_bytes.items()):
            archive.writestr(path, raw)
    result = output.getvalue()
    if len(result) > MAX_ARCHIVE_BYTES:
        raise PortabilityError(
            "archive_too_large", "Workspace export exceeds the archive size limit."
        )
    return result


def import_workspace(
    session: Session, content: bytes, settings: Any, *, label: str | None = None
) -> dict[str, Any]:
    manifest, assets = _read_archive(content)
    workspace_info = manifest.workspace
    old_workspace_id = _required_uuid(workspace_info.get("id"), "workspace.id")
    old_label = _required_string(workspace_info.get("label"), "workspace.label", 120)
    entities = manifest.entities
    if set(entities) != ENTITY_KEYS:
        raise PortabilityError(
            "unsupported_schema", "The archive entity set is incomplete or unsupported."
        )
    for name, entity_records in entities.items():
        if len(entity_records) > 100_000:
            raise PortabilityError(
                "too_many_records", f"Archive has too many {name} records."
            )

    # IDs are always regenerated. This makes the returned map explicit and keeps
    # a valid import independent of whether an old UUID already exists locally.
    id_map: dict[str, str] = {old_workspace_id: str(uuid4())}
    for entity_records in entities.values():
        for record in entity_records:
            old_id = _required_uuid(record.get("id"), "entity.id")
            if old_id in id_map:
                raise PortabilityError(
                    "duplicate_id", "Archive contains duplicate or invalid record IDs."
                )
            id_map[old_id] = str(uuid4())
    _validate_entity_references(entities, id_map)
    _validate_asset_ownership(manifest, entities)

    new_workspace = Workspace(
        id=id_map[old_workspace_id], label=(label or old_label)[:120]
    )
    session.add(new_workspace)
    session.flush()
    entity_map = entities
    sources = []
    for row in entity_map["sources"]:
        old_source_id = _required_string(row.get("id"), "source.id", 36)
        kind = _required_string(row.get("kind"), "source.kind", 30)
        details = _safe_source_details(row.get("details"))
        original_asset = _asset_for(assets, "source", old_source_id, "original")
        if kind in {"mysql", "postgresql"}:
            details["portability"] = {
                "reconnection_required": True,
                "connection": _safe_connection_descriptor(row.get("connection")),
            }
        elif original_asset is None and row.get("original_unavailable"):
            details["portability"] = {"original_unavailable": True}
        stored_asset = original_asset
        storage_key = None
        if stored_asset is not None:
            storage_key = _store_imported_asset(
                settings, id_map[old_source_id], "original", stored_asset
            )
        source = Source(
            id=id_map[old_source_id],
            workspace_id=new_workspace.id,
            kind=kind,
            version=_integer(row.get("version"), "source.version", minimum=1),
            display_name=_required_string(
                row.get("display_name"), "source.display_name", 255
            ),
            state=(
                "disconnected"
                if kind in {"mysql", "postgresql"}
                else _portable_source_state(row.get("state"))
            ),
            storage_key=storage_key,
            content_hash=(
                stored_asset["sha256"]
                if stored_asset
                else _optional_hash(row.get("content_hash"))
            ),
            schema_version=_optional_string(row.get("schema_version"), 64),
            details=details,
        )
        sources.append(source)
    session.add_all(sources)
    session.flush()

    documents = []
    for row in entity_map["documents"]:
        documents.append(
            Document(
                id=id_map[row["id"]],
                source_id=_mapped(id_map, row.get("source_id")),
                source_version=_integer(
                    row.get("source_version"), "document.source_version", minimum=1
                ),
                extractor_version=_required_string(
                    row.get("extractor_version"), "document.extractor_version", 120
                ),
                chunker_version=_required_string(
                    row.get("chunker_version"), "document.chunker_version", 120
                ),
                state=_portable_document_state(row.get("state")),
                stage="imported",
                progress=100,
                details={
                    **_remap_json(
                        _json_object(row.get("details"), "document.details"), id_map
                    ),
                    "portability": {"reindex_required": True},
                },
                index_generation_id=None,
            )
        )
    session.add_all(documents)
    session.flush()

    session.add_all(
        [
            DocumentBlock(
                id=id_map[row["id"]],
                document_id=_mapped(id_map, row.get("document_id")),
                ordinal=_integer(row.get("ordinal"), "block.ordinal", minimum=0),
                kind=_required_string(row.get("kind"), "block.kind", 30),
                text=_string(row.get("text"), "block.text"),
                heading=_optional_string(row.get("heading")),
                location=_json_object(row.get("location"), "block.location"),
                language=_required_string(row.get("language"), "block.language", 20),
                scripts=_string_list(row.get("scripts"), "block.scripts"),
            )
            for row in entity_map["blocks"]
        ]
    )
    session.add_all(
        [
            DocumentChunk(
                id=id_map[row["id"]],
                document_id=_mapped(id_map, row.get("document_id")),
                ordinal=_integer(row.get("ordinal"), "chunk.ordinal", minimum=0),
                chunker_version=_required_string(
                    row.get("chunker_version"), "chunk.chunker_version", 120
                ),
                text=_string(row.get("text"), "chunk.text"),
                normalized_text=_string(
                    row.get("normalized_text"), "chunk.normalized_text"
                ),
                heading=_optional_string(row.get("heading")),
                location=_json_object(row.get("location"), "chunk.location"),
                language=_required_string(row.get("language"), "chunk.language", 20),
                block_ids=_mapped_list(id_map, row.get("block_ids"), "chunk.block_ids"),
                token_count=_integer(
                    row.get("token_count"), "chunk.token_count", minimum=0
                ),
                parent_id=_mapped_optional(id_map, row.get("parent_id")),
                previous_id=_mapped_optional(id_map, row.get("previous_id")),
                next_id=_mapped_optional(id_map, row.get("next_id")),
            )
            for row in entity_map["chunks"]
        ]
    )

    datasets = []
    for row in entity_map["datasets"]:
        asset = _asset_for(assets, "dataset", row["id"], "dataset")
        datasets.append(
            Dataset(
                id=id_map[row["id"]],
                source_id=_mapped(id_map, row.get("source_id")),
                source_version=_integer(
                    row.get("source_version"), "dataset.source_version", minimum=1
                ),
                identity=_required_string(row.get("identity"), "dataset.identity", 512),
                schema_version=_required_string(
                    row.get("schema_version"), "dataset.schema_version", 64
                ),
                details=_remap_json(
                    _json_object(row.get("details"), "dataset.details"), id_map
                ),
                storage_key=(
                    _store_imported_asset(settings, id_map[row["id"]], "dataset", asset)
                    if asset
                    else None
                ),
                designation=_required_string(
                    row.get("designation"), "dataset.designation", 20
                ),
                lineage=_remap_json(
                    _string_list(row.get("lineage"), "dataset.lineage"), id_map
                ),
            )
        )
    session.add_all(datasets)

    threads = [
        Thread(
            id=id_map[row["id"]],
            workspace_id=new_workspace.id,
            label=_required_string(row.get("label"), "thread.label", 120),
            created_at=_datetime(row.get("created_at")),
        )
        for row in entity_map["threads"]
    ]
    session.add_all(threads)
    session.flush()
    session.add_all(
        [
            Run(
                id=id_map[row["id"]],
                thread_id=_mapped(id_map, row.get("thread_id")),
                state="imported",
                selected_source_ids=_mapped_list(
                    id_map, row.get("selected_source_ids"), "run.selected_source_ids"
                ),
                config={
                    "imported": True,
                    "original_state": _safe_run_state(row.get("state")),
                    **_remap_json(_safe_run_config(row.get("config")), id_map),
                },
                outcome=_remap_json(_safe_outcome(row.get("outcome")), id_map),
                event_sequence=0,
                started_at=None,
                finished_at=_datetime(row.get("finished_at")),
            )
            for row in entity_map["runs"]
        ]
    )
    session.flush()
    session.add_all(
        [
            Message(
                id=id_map[row["id"]],
                thread_id=_mapped(id_map, row.get("thread_id")),
                run_id=_mapped_optional(id_map, row.get("run_id")),
                role=_required_string(row.get("role"), "message.role", 20),
                content=_remap_citation_tokens(
                    _string(row.get("content"), "message.content"), id_map
                ),
                selected_source_ids=_mapped_list(
                    id_map,
                    row.get("selected_source_ids"),
                    "message.selected_source_ids",
                ),
                references=_remap_json(
                    _json_object(row.get("references"), "message.references"), id_map
                ),
                created_at=_datetime(row.get("created_at")),
            )
            for row in entity_map["messages"]
        ]
    )
    session.add_all(
        [
            Evidence(
                id=id_map[row["id"]],
                run_id=_mapped(id_map, row.get("run_id")),
                kind=_required_string(row.get("kind"), "evidence.kind", 30),
                source_ids=_mapped_list(
                    id_map, row.get("source_ids"), "evidence.source_ids"
                ),
                details=_remap_json(
                    _json_object(row.get("details"), "evidence.details"), id_map
                ),
                created_at=_datetime(row.get("created_at")),
            )
            for row in entity_map["evidence"]
        ]
    )

    imported_artifacts = []
    for row in entity_map["artifacts"]:
        asset = _asset_for(assets, "artifact", row["id"], "artifact")
        available = asset is not None
        key = (
            _store_imported_asset(settings, id_map[row["id"]], "artifact", asset)
            if asset is not None
            else f"derived/imported-unavailable/{id_map[row['id']]}"
        )
        sha256 = (
            asset["sha256"]
            if asset is not None
            else _optional_hash(row.get("sha256")) or "0" * 64
        )
        byte_size = (
            asset["byte_size"]
            if asset is not None
            else _integer(row.get("byte_size"), "artifact.byte_size", minimum=0)
        )
        imported_artifacts.append(
            Artifact(
                id=id_map[row["id"]],
                run_id=_mapped_optional(id_map, row.get("run_id")),
                tool_call_id=None,
                storage_key=key,
                display_name=_required_string(
                    row.get("display_name"), "artifact.display_name", 255
                ),
                media_type=_required_string(
                    row.get("media_type"), "artifact.media_type", 120
                ),
                byte_size=byte_size,
                sha256=sha256,
                lineage=_remap_json(
                    _string_list(row.get("lineage"), "artifact.lineage"), id_map
                ),
                durable=asset is not None,
                created_at=_datetime(row.get("created_at")),
            )
        )
    session.add_all(imported_artifacts)

    # Keep only known deterministic summaries; rewrite all provenance IDs and
    # namespace fingerprints because IDs are regenerated in the destination.
    for row in entity_map["summaries"]:
        payload = row.get("payload")
        if not isinstance(payload, dict) or not _supported_summary(payload):
            continue
        remapped = _remap_json(payload, id_map)
        old_fingerprint = _required_string(
            row.get("fingerprint"), "summary.fingerprint", 64
        )
        fingerprint = hashlib.sha256(
            f"portable-import:{new_workspace.id}:{old_fingerprint}".encode()
        ).hexdigest()
        session.add(
            SummaryCache(
                workspace_id=new_workspace.id,
                id=id_map[row["id"]],
                fingerprint=fingerprint,
                scope=_required_string(row.get("scope"), "summary.scope", 20),
                payload=remapped,
                created_at=_datetime(row.get("created_at")),
            )
        )

    session.flush()
    session.commit()
    reconnection_required = []
    for row in entity_map["sources"]:
        if row.get("kind") in {"mysql", "postgresql"}:
            descriptor = _safe_connection_descriptor(row.get("connection"))
            reconnection_required.append(
                {
                    "source_id": id_map[row["id"]],
                    "display_name": row.get("display_name"),
                    **descriptor,
                }
            )
    return {
        "workspace": {"id": new_workspace.id, "label": new_workspace.label},
        "id_map": id_map,
        "reconnection_required": reconnection_required,
        "reindex_required": [id_map[row["id"]] for row in entity_map["documents"]],
    }


def _read_archive(content: bytes) -> tuple[PortableManifest, dict[str, Any]]:
    if not content or len(content) > MAX_ARCHIVE_BYTES:
        raise PortabilityError(
            "archive_size_invalid", "Archive is empty or exceeds the upload limit.", 413
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(content), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise PortabilityError(
            "invalid_archive", "Upload a valid portable ZIP archive."
        ) from exc
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_MEMBERS:
            raise PortabilityError(
                "archive_member_limit", "Archive has an invalid number of entries."
            )
        names: set[str] = set()
        expanded = 0
        for info in infos:
            _validate_member(info)
            if info.filename in names:
                raise PortabilityError(
                    "duplicate_archive_path", "Archive contains duplicate paths."
                )
            names.add(info.filename)
            expanded += info.file_size
            if info.file_size > (
                MAX_MANIFEST_BYTES
                if info.filename == "manifest.json"
                else MAX_MEMBER_BYTES
            ):
                raise PortabilityError(
                    "member_too_large", "An archive entry exceeds its size limit."
                )
            if info.compress_size == 0 and info.file_size > 0:
                raise PortabilityError(
                    "invalid_compression",
                    "Archive contains an invalid compressed entry.",
                )
            if info.compress_size and info.file_size > max(
                1024 * 1024, info.compress_size * MAX_COMPRESSION_RATIO
            ):
                raise PortabilityError(
                    "compression_ratio_exceeded",
                    "Archive expansion ratio exceeds the safety limit.",
                )
        if expanded > MAX_EXPANDED_BYTES:
            raise PortabilityError(
                "expanded_size_exceeded", "Archive expands beyond the safety limit."
            )
        if "manifest.json" not in names:
            raise PortabilityError(
                "manifest_missing", "Portable archive has no manifest."
            )
        manifest_raw = _read_member(archive, "manifest.json", MAX_MANIFEST_BYTES)
        try:
            raw = json.loads(
                manifest_raw.decode("utf-8"), object_pairs_hook=_unique_object
            )
            manifest = PortableManifest.model_validate(raw)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise PortabilityError(
                "manifest_invalid",
                "Portable archive manifest is invalid or unsupported.",
            ) from exc
        if set(manifest.entities) != ENTITY_KEYS:
            raise PortabilityError(
                "unsupported_schema", "Portable archive entity schema is unsupported."
            )
        if len(manifest.assets) > MAX_ASSETS:
            raise PortabilityError(
                "asset_limit_exceeded", "Archive contains too many assets."
            )
        declared = {item.path for item in manifest.assets}
        if len(declared) != len({name for name in names if name != "manifest.json"}):
            raise PortabilityError(
                "asset_manifest_mismatch",
                "Archive entries do not match the asset manifest.",
            )
        if names - {"manifest.json"} != declared:
            raise PortabilityError(
                "asset_manifest_mismatch",
                "Archive contains unlisted or missing assets.",
            )
        contents = {}
        for asset in manifest.assets:
            raw_bytes = _read_member(archive, asset.path, MAX_MEMBER_BYTES)
            if (
                len(raw_bytes) != asset.byte_size
                or hashlib.sha256(raw_bytes).hexdigest() != asset.sha256
            ):
                raise PortabilityError(
                    "asset_hash_mismatch",
                    "An archive asset failed hash or size validation.",
                )
            contents[asset.path] = raw_bytes
        for asset in manifest.assets:
            if asset.path.rsplit("/", 1)[-1] != asset.sha256:
                raise PortabilityError(
                    "asset_path_mismatch",
                    "An archive asset path does not match its content hash.",
                )
        descriptors = [item.model_dump() for item in manifest.assets]
        return manifest, {**contents, "__descriptors__": descriptors}


def _validate_asset_ownership(
    manifest: PortableManifest, entities: dict[str, list[dict[str, Any]]]
) -> None:
    ids_by_type = {
        "source": {row.get("id") for row in entities["sources"]},
        "dataset": {row.get("id") for row in entities["datasets"]},
        "artifact": {row.get("id") for row in entities["artifacts"]},
    }
    seen: set[tuple[str, str, str]] = set()
    for asset in manifest.assets:
        key = (asset.owner_type, asset.owner_id, asset.purpose)
        if asset.owner_id not in ids_by_type[asset.owner_type] or key in seen:
            raise PortabilityError(
                "invalid_asset_owner", "An asset has an unknown or duplicate owner."
            )
        if asset.purpose != asset.owner_type and not (
            asset.owner_type == "source" and asset.purpose == "original"
        ):
            raise PortabilityError(
                "invalid_asset_owner", "Asset purpose does not match its owner."
            )
        seen.add(key)

    for source in entities["sources"]:
        if (
            source.get("original_available")
            and ("source", source.get("id"), "original") not in seen
        ):
            raise PortabilityError(
                "asset_manifest_mismatch",
                "A source original is missing from the archive.",
            )
    for artifact in entities["artifacts"]:
        if (
            artifact.get("durable")
            and ("artifact", artifact.get("id"), "artifact") not in seen
        ):
            raise PortabilityError(
                "asset_manifest_mismatch",
                "A durable artifact is missing from the archive.",
            )
    for dataset in entities["datasets"]:
        if (
            dataset.get("storage_available")
            and ("dataset", dataset.get("id"), "dataset") not in seen
        ):
            raise PortabilityError(
                "asset_manifest_mismatch",
                "A stored dataset is missing from the archive.",
            )


def _validate_entity_references(
    entities: dict[str, list[dict[str, Any]]], id_map: dict[str, str]
) -> None:
    for row in entities["documents"]:
        _mapped(id_map, row.get("source_id"))
    for row in entities["blocks"] + entities["chunks"]:
        _mapped(id_map, row.get("document_id"))
    for row in entities["chunks"]:
        _mapped_list(id_map, row.get("block_ids"), "chunk.block_ids")
        for key in ("parent_id", "previous_id", "next_id"):
            _mapped_optional(id_map, row.get(key))
    for row in entities["datasets"]:
        _mapped(id_map, row.get("source_id"))
    for row in entities["runs"]:
        _mapped(id_map, row.get("thread_id"))
        _mapped_list(id_map, row.get("selected_source_ids"), "run.selected_source_ids")
    for row in entities["messages"]:
        _mapped(id_map, row.get("thread_id"))
        _mapped_optional(id_map, row.get("run_id"))
        _mapped_list(
            id_map, row.get("selected_source_ids"), "message.selected_source_ids"
        )
    for row in entities["evidence"]:
        _mapped(id_map, row.get("run_id"))
        _mapped_list(id_map, row.get("source_ids"), "evidence.source_ids")
    for row in entities["artifacts"]:
        _mapped_optional(id_map, row.get("run_id"))


def _validate_member(info: zipfile.ZipInfo) -> None:
    name = info.filename
    path = PurePosixPath(name)
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if (
        not name
        or name.startswith(("/", "\\"))
        or "\\" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or (mode and stat.S_ISLNK(mode))
        or file_type not in {0, stat.S_IFREG}
        or info.flag_bits & 0x1
        or info.is_dir()
        or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
    ):
        raise PortabilityError(
            "unsafe_archive_path",
            "Archive paths must be regular files under the archive root.",
        )
    if name != "manifest.json" and (len(path.parts) != 2 or path.parts[0] != "assets"):
        raise PortabilityError(
            "unsafe_archive_path",
            "Archive contains a file outside the asset directory.",
        )


def _read_member(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    try:
        with archive.open(name) as stream:
            content = stream.read(limit + 1)
    except (KeyError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise PortabilityError(
            "invalid_archive_entry", "Archive entry could not be read."
        ) from exc
    if len(content) > limit:
        raise PortabilityError(
            "member_too_large", "An archive entry exceeds its size limit."
        )
    return content


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _many(session: Session, model: Any, condition: Any) -> list[Any]:
    return list(session.scalars(select(model).where(condition).order_by(model.id)))


def _portable_summaries(
    session: Session, source_ids: list[str]
) -> list[dict[str, Any]]:
    if not source_ids:
        return []
    rows = session.scalars(select(SummaryCache).order_by(SummaryCache.id)).all()
    allowed = set(source_ids)
    result = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        if not _supported_summary(payload):
            continue
        mentioned = {
            doc.get("source_id")
            for doc in payload.get("documents", [])
            if isinstance(doc, dict)
        }
        if mentioned and mentioned.issubset(allowed):
            result.append(
                {
                    "id": row.id,
                    "fingerprint": row.fingerprint,
                    "scope": row.scope,
                    "payload": payload,
                    "created_at": _iso(row.created_at),
                }
            )
    return result


def _asset_for(
    assets: dict[str, Any], owner_type: str, owner_id: str, purpose: str
) -> dict[str, Any] | None:
    descriptors: list[dict[str, Any]] = assets.get("__descriptors__", [])
    for item in descriptors:
        if (
            item["owner_type"] == owner_type
            and item["owner_id"] == owner_id
            and item["purpose"] == purpose
        ):
            return {**item, "content": assets[item["path"]]}
    return None


def _store_imported_asset(
    settings: Any, owner_id: str, purpose: str, asset: dict[str, Any]
) -> str:
    namespace = "originals" if purpose == "original" else "derived"
    key = f"{namespace}/imports/{owner_id}/{purpose}/{asset['sha256']}"
    stored = get_storage(settings).put(key, asset["content"])
    if stored.sha256 != asset["sha256"] or stored.byte_size != asset["byte_size"]:
        raise PortabilityError(
            "asset_store_verification_failed",
            "Imported asset failed storage verification.",
            503,
        )
    return stored.key


def _source_record(row: Source, connection: Connection | None) -> dict[str, Any]:
    record = {
        "id": row.id,
        "kind": row.kind,
        "version": row.version,
        "display_name": row.display_name,
        "state": row.state,
        "content_hash": row.content_hash,
        "schema_version": row.schema_version,
        "details": _safe_source_details(row.details),
    }
    record["connection"] = (
        {
            "dialect": connection.dialect,
            "host": connection.host,
            "port": connection.port,
            "database_name": connection.database_name,
            "username": connection.username,
            "options": connection.options,
        }
        if connection is not None
        else _safe_connection_descriptor(
            row.details.get("portability", {}).get("connection")
            if isinstance(row.details, dict)
            and isinstance(row.details.get("portability"), dict)
            else None
        )
    )
    record["original_available"] = bool(row.storage_key)
    record["original_unavailable"] = bool(row.storage_key is None and row.content_hash)
    return record


def _document_record(row: Document) -> dict[str, Any]:
    safe_keys = {
        "warnings",
        "languages",
        "chunk_strategy",
        "page_count",
        "extractor",
        "tables",
        "layout_profile",
    }
    details = row.details if isinstance(row.details, dict) else {}
    return {
        "id": row.id,
        "source_id": row.source_id,
        "source_version": row.source_version,
        "extractor_version": row.extractor_version,
        "chunker_version": row.chunker_version,
        "state": row.state,
        "details": {k: details[k] for k in safe_keys if k in details},
    }


def _block_record(row: DocumentBlock) -> dict[str, Any]:
    return {
        "id": row.id,
        "document_id": row.document_id,
        "ordinal": row.ordinal,
        "kind": row.kind,
        "text": row.text,
        "heading": row.heading,
        "location": row.location,
        "language": row.language,
        "scripts": row.scripts,
        "created_at": _iso(row.created_at),
    }


def _chunk_record(row: DocumentChunk) -> dict[str, Any]:
    return {
        "id": row.id,
        "document_id": row.document_id,
        "ordinal": row.ordinal,
        "chunker_version": row.chunker_version,
        "text": row.text,
        "normalized_text": row.normalized_text,
        "heading": row.heading,
        "location": row.location,
        "language": row.language,
        "block_ids": row.block_ids,
        "token_count": row.token_count,
        "parent_id": row.parent_id,
        "previous_id": row.previous_id,
        "next_id": row.next_id,
        "created_at": _iso(row.created_at),
    }


def _dataset_record(row: Dataset) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_id": row.source_id,
        "source_version": row.source_version,
        "identity": row.identity,
        "schema_version": row.schema_version,
        "details": _json_object(row.details, "dataset.details"),
        "designation": row.designation,
        "lineage": row.lineage,
        "storage_available": bool(row.storage_key),
        "created_at": _iso(row.created_at),
    }


def _thread_record(row: Thread) -> dict[str, Any]:
    return {"id": row.id, "label": row.label, "created_at": _iso(row.created_at)}


def _run_record(row: Run) -> dict[str, Any]:
    return {
        "id": row.id,
        "thread_id": row.thread_id,
        "state": row.state,
        "selected_source_ids": row.selected_source_ids,
        "config": _safe_run_config(row.config),
        "outcome": _safe_outcome(row.outcome),
        "finished_at": _iso(row.finished_at),
        "created_at": _iso(row.created_at),
    }


def _message_record(row: Message) -> dict[str, Any]:
    return {
        "id": row.id,
        "thread_id": row.thread_id,
        "run_id": row.run_id,
        "role": row.role,
        "content": row.content,
        "selected_source_ids": row.selected_source_ids,
        "references": row.references,
        "created_at": _iso(row.created_at),
    }


def _evidence_record(row: Evidence) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "kind": row.kind,
        "source_ids": row.source_ids,
        "details": row.details,
        "created_at": _iso(row.created_at),
    }


def _artifact_record(row: Artifact) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "display_name": row.display_name,
        "media_type": row.media_type,
        "sha256": row.sha256,
        "byte_size": row.byte_size,
        "lineage": row.lineage,
        "durable": row.durable,
        "created_at": _iso(row.created_at),
    }


def _safe_source_details(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "description",
        "metric_hints",
        "media_type",
        "columns",
        "sheet_names",
        "table_names",
        "format",
        "portability",
    }
    return {key: value[key] for key in allowed if key in value and key != "portability"}


def _safe_connection_descriptor(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    safe: dict[str, Any] = {}
    for key, maximum in (
        ("dialect", 20),
        ("host", 255),
        ("database_name", 255),
        ("username", 255),
    ):
        item = value.get(key)
        if isinstance(item, str) and not (
            key == "host" and ("@" in item or "://" in item)
        ):
            safe[key] = item[:maximum]
    port = value.get("port")
    if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535:
        safe["port"] = port
    options = value.get("options")
    if isinstance(options, dict):
        safe["options"] = {
            key: options[key]
            for key in ("ssl_mode", "connect_timeout_seconds", "schema")
            if key in options
        }
    return safe


def _safe_outcome(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "answer",
        "text",
        "clarification",
        "status",
        "evidence_ids",
        "artifact_ids",
        "language",
    }
    return {key: value[key] for key in allowed if key in value}


def _safe_run_config(value: Any) -> dict[str, Any]:
    """Keep only non-secret settings needed to explain imported run provenance."""
    if not isinstance(value, dict):
        return {}
    safe: dict[str, Any] = {}
    profile = value.get("retrieval_profile")
    if isinstance(profile, str) and profile in {"basic", "advanced"}:
        safe["retrieval_profile"] = profile
    for key, maximum in (("answer_language", 20), ("prompt_version", 120)):
        item = value.get(key)
        if isinstance(item, str) and item and len(item) <= maximum:
            safe[key] = item
    selected = value.get("selected_dataset_ids")
    if isinstance(selected, list) and len(selected) <= 100:
        safe_ids = []
        for item in selected:
            try:
                safe_ids.append(_required_uuid(item, "config.selected_dataset_ids"))
            except PortabilityError:
                continue
        safe["selected_dataset_ids"] = safe_ids
    aliases = value.get("reference_aliases")
    if isinstance(aliases, dict):
        from app.agent.references import ModelReferences

        try:
            safe["reference_aliases"] = ModelReferences(aliases).aliases
        except (ValueError, TypeError):
            raise PortabilityError(
                "invalid_reference_aliases", "Invalid retained reference mapping"
            ) from None
    versions = value.get("source_versions")
    if isinstance(versions, dict) and len(versions) <= 100:
        safe_versions = {}
        for identity, version in versions.items():
            if (
                isinstance(identity, str)
                and isinstance(version, int)
                and not isinstance(version, bool)
                and version >= 1
            ):
                try:
                    safe_versions[
                        _required_uuid(identity, "config.source_versions")
                    ] = version
                except PortabilityError:
                    continue
        safe["source_versions"] = safe_versions
    return safe


def _remap_citation_tokens(content: str, id_map: dict[str, str]) -> str:
    token = re.compile(
        r"\[(evidence|artifact):([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\]"
    )
    return token.sub(
        lambda match: f"[{match.group(1)}:{id_map.get(match.group(2), match.group(2))}]",
        content,
    )


def _supported_summary(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    method = value.get("method")
    return (
        isinstance(method, dict)
        and method.get("algorithm") in SUPPORTED_SUMMARY_ALGORITHMS
    )


def _safe_run_state(value: Any) -> str:
    return value[:30] if isinstance(value, str) else "unknown"


def _remap_json(value: Any, id_map: dict[str, str]) -> Any:
    if isinstance(value, str):
        if value in id_map:
            return id_map[value]
        return re.sub(
            r"(?<![0-9a-fA-F])([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?![0-9a-fA-F])",
            lambda match: id_map.get(match.group(1), match.group(1)),
            value,
        )
    if isinstance(value, list):
        return [_remap_json(item, id_map) for item in value]
    if isinstance(value, dict):
        return {
            id_map.get(key, key) if isinstance(key, str) else key: _remap_json(
                item, id_map
            )
            for key, item in value.items()
        }
    return value


def _mapped(id_map: dict[str, str], value: Any) -> str:
    if not isinstance(value, str) or value not in id_map:
        raise PortabilityError(
            "invalid_reference", "Archive contains a dangling internal reference."
        )
    return id_map[value]


def _mapped_optional(id_map: dict[str, str], value: Any) -> str | None:
    return None if value is None else _mapped(id_map, value)


def _mapped_list(id_map: dict[str, str], value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise PortabilityError("invalid_manifest_field", f"{name} must be a list.")
    return [_mapped(id_map, item) for item in value]


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PortabilityError(
            "invalid_timestamp", "Archive timestamp must be an ISO string."
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PortabilityError(
            "invalid_timestamp", "Archive timestamp is invalid."
        ) from exc
    if parsed.tzinfo is None:
        raise PortabilityError(
            "invalid_timestamp", "Archive timestamp must include a timezone."
        )
    return parsed


def _required_string(value: Any, name: str, max_length: int) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise PortabilityError(
            "invalid_manifest_field", f"{name} is missing or invalid."
        )
    return value


def _required_uuid(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise PortabilityError("invalid_manifest_field", f"{name} must be a UUID.")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise PortabilityError(
            "invalid_manifest_field", f"{name} must be a UUID."
        ) from exc


def _optional_string(value: Any, max_length: int = 4000) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_length:
        raise PortabilityError(
            "invalid_manifest_field", "Optional string field is invalid."
        )
    return value


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise PortabilityError("invalid_manifest_field", f"{name} must be a string.")
    return value


def _integer(value: Any, name: str, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise PortabilityError("invalid_manifest_field", f"{name} is invalid.")
    return value


def _state(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 30:
        return "imported"
    return value


def _portable_source_state(value: Any) -> str:
    if value in {"processing", "queued", "running"}:
        return "uploaded"
    return _state(value)


def _portable_document_state(value: Any) -> str:
    if value == "ready":
        return "ready"
    if value == "archived":
        return "archived"
    return "failed"


def _optional_hash(value: Any) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise PortabilityError("invalid_manifest_field", "Source hash is invalid.")
    return value


def _string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PortabilityError(
            "invalid_manifest_field", f"{name} must be a list of strings."
        )
    return value


def _json_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PortabilityError("invalid_manifest_field", f"{name} must be an object.")
    return value
