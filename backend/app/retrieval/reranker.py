"""Offline-only local cross-encoder scoring for retrieval candidates."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings

_MANIFEST_NAME = "analyst-reranker-model.json"
_SUPPORTED_MODELS = {
    "jinaai/jina-reranker-v2-base-multilingual",
    "BAAI/bge-reranker-base",
}
_MAX_QUERY_CHARACTERS = 2_000
_MAX_PASSAGES = 128
_MAX_PASSAGE_CHARACTERS = 12_000
_MAX_BATCH_CHARACTERS = 400_000
_BATCH_SIZE = 8
_ADAPTER_LOCK = threading.Lock()
_ADAPTERS: dict[tuple[str, str, str, int], "FastEmbedCrossEncoderAdapter"] = {}


class RerankerUnavailable(RuntimeError):
    """Local reranker assets or inference are unavailable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RerankerAdapter(Protocol):
    model_id: str
    revision: str

    def score(self, query: str, passages: list[str]) -> list[float]: ...


def _resolved_model_path(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    backend_relative = Path(__file__).resolve().parents[2] / path
    if backend_relative.exists():
        return backend_relative.resolve()
    return path.resolve()


def _manifest(model_path: Path, settings: Settings) -> dict[str, Any]:
    model_id = settings.reranker_model
    if model_id not in _SUPPORTED_MODELS:
        raise RerankerUnavailable(
            "reranker_unavailable",
            "The configured local reranker model is unavailable.",
        )
    root = _resolved_model_path(model_path)
    manifest_path = root / _MANIFEST_NAME
    if not root.is_dir() or not manifest_path.is_file():
        raise RerankerUnavailable(
            "reranker_assets_missing", "Pinned local reranker assets are unavailable."
        )
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RerankerUnavailable(
            "reranker_manifest_invalid", "The local reranker manifest is invalid."
        ) from None
    if not isinstance(value, dict):
        raise RerankerUnavailable(
            "reranker_manifest_invalid", "The local reranker manifest is invalid."
        )

    manifest_model = value.get("model_id")
    revision = value.get("revision")
    model_file = value.get("model_file")
    file_hashes = value.get("sha256")
    if (
        manifest_model != model_id
        or not isinstance(revision, str)
        or not revision
        or (settings.reranker_revision and revision != settings.reranker_revision)
        or not isinstance(model_file, str)
        or model_file != "onnx/model.onnx"
        or not isinstance(file_hashes, dict)
        or not file_hashes
        or model_file not in file_hashes
    ):
        raise RerankerUnavailable(
            "reranker_manifest_mismatch",
            "The local reranker manifest does not match the configured model.",
        )

    for relative_name, expected_hash in file_hashes.items():
        if not isinstance(relative_name, str) or not isinstance(expected_hash, str):
            raise RerankerUnavailable(
                "reranker_manifest_invalid", "The local reranker manifest is invalid."
            )
        file_path = (root / relative_name).resolve()
        if root not in file_path.parents or not file_path.is_file():
            raise RerankerUnavailable(
                "reranker_assets_missing", "A pinned local reranker file is missing."
            )
        try:
            actual_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        except OSError:
            raise RerankerUnavailable(
                "reranker_assets_missing", "A pinned local reranker file is unreadable."
            ) from None
        if actual_hash != expected_hash:
            raise RerankerUnavailable(
                "reranker_asset_checksum_mismatch",
                "A pinned local reranker file failed its checksum check.",
            )
    return value


class FastEmbedCrossEncoderAdapter:
    """FastEmbed cross encoder that can only load verified local model files."""

    def __init__(self, settings: Settings) -> None:
        model_path = settings.reranker_model_path
        if model_path is None:
            raise RerankerUnavailable(
                "reranker_unavailable", "Local reranker model files are not configured."
            )
        manifest = _manifest(model_path, settings)
        self.model_id = str(manifest["model_id"])
        self.revision = str(manifest["revision"])
        self._model = _load_fastembed(settings, manifest)

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not isinstance(query, str) or not query.strip():
            raise RerankerUnavailable("empty_query", "Reranker query cannot be empty.")
        query = query.strip()
        if len(query) > _MAX_QUERY_CHARACTERS:
            raise RerankerUnavailable(
                "reranker_input_too_large", "Reranker query exceeds its size limit."
            )
        if not isinstance(passages, list) or any(
            not isinstance(passage, str) for passage in passages
        ):
            raise RerankerUnavailable(
                "reranker_invalid_input", "Reranker passages must be a list of strings."
            )
        if len(passages) > _MAX_PASSAGES:
            raise RerankerUnavailable(
                "reranker_input_too_large",
                "Too many passages were sent to the reranker.",
            )
        if any(len(passage) > _MAX_PASSAGE_CHARACTERS for passage in passages):
            raise RerankerUnavailable(
                "reranker_input_too_large", "A passage exceeds the reranker size limit."
            )
        if sum(map(len, passages)) + len(query) > _MAX_BATCH_CHARACTERS:
            raise RerankerUnavailable(
                "reranker_input_too_large",
                "Reranker input exceeds its total size limit.",
            )
        if not passages:
            return []

        try:
            raw_scores = list(
                self._model.rerank(query, passages, batch_size=_BATCH_SIZE)
            )
        except Exception:
            raise RerankerUnavailable(
                "reranker_inference_failed", "Local reranker inference failed."
            ) from None
        if len(raw_scores) != len(passages):
            raise RerankerUnavailable(
                "reranker_output_mismatch",
                "The reranker returned an incomplete score batch.",
            )
        try:
            scores = [float(score) for score in raw_scores]
        except (TypeError, ValueError, OverflowError):
            raise RerankerUnavailable(
                "reranker_invalid_score", "The reranker returned an invalid score."
            ) from None
        if any(not math.isfinite(score) for score in scores):
            raise RerankerUnavailable(
                "reranker_invalid_score", "The reranker returned an invalid score."
            )
        return scores


def _load_fastembed(settings: Settings, manifest: dict[str, Any]) -> Any:
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("ORT_DISABLE_TELEMETRY_EVENTS", "1")
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
    except ImportError:
        raise RerankerUnavailable(
            "reranker_unavailable", "FastEmbed cross-encoder support is unavailable."
        ) from None

    model_path = settings.reranker_model_path
    assert model_path is not None
    try:
        return TextCrossEncoder(
            model_name=str(manifest["model_id"]),
            specific_model_path=str(_resolved_model_path(model_path)),
            threads=settings.embedding_threads,
            local_files_only=True,
        )
    except Exception:
        raise RerankerUnavailable(
            "reranker_model_load_failed",
            "Pinned local reranker model could not be loaded.",
        ) from None


def get_reranker_adapter(settings: Settings) -> RerankerAdapter:
    """Load the checksum-verified local model once; never download assets."""
    model_path = settings.reranker_model_path
    if model_path is None:
        raise RerankerUnavailable(
            "reranker_unavailable", "Local reranker model files are not configured."
        )
    key = (
        settings.reranker_model or "",
        str(_resolved_model_path(model_path)),
        settings.reranker_revision or "manifest",
        settings.embedding_threads,
    )
    with _ADAPTER_LOCK:
        adapter = _ADAPTERS.get(key)
        if adapter is None:
            adapter = FastEmbedCrossEncoderAdapter(settings)
            _ADAPTERS[key] = adapter
        return adapter
