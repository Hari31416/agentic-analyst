"""Offline-only local dense embeddings for document retrieval."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings

_DEFAULT_MODEL = "google/embeddinggemma-2"
_LEGACY_MODEL = "intfloat/multilingual-e5-small"
_SUPPORTED_MODELS = {_DEFAULT_MODEL, _LEGACY_MODEL}
_MANIFEST_NAME = "analyst-model.json"
_REGISTRATION_LOCK = threading.Lock()
_ADAPTER_LOCK = threading.Lock()
_REGISTERED_MODELS: set[str] = set()
_ADAPTERS: dict[tuple[str, str, str, int, int], "EmbeddingAdapter"] = {}


class EmbeddingUnavailable(RuntimeError):
    """Local embedding assets or inference are unavailable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EmbeddingAdapter(Protocol):
    model_id: str
    revision: str
    dimensions: int

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def embed_multimodal(self, items: list[dict[str, Any]]) -> list[list[float]]: ...


def _manifest(model_path: Path, settings: Settings) -> dict[str, Any]:
    if settings.embedding_model not in _SUPPORTED_MODELS:
        raise EmbeddingUnavailable(
            "embedding_unavailable",
            "The configured local embedding model is unavailable.",
        )
    if settings.embedding_model_path is None:
        raise EmbeddingUnavailable(
            "embedding_unavailable", "Local embedding model files are not configured."
        )
    root = _resolved_model_path(model_path)
    manifest_path = root / _MANIFEST_NAME
    if not root.is_dir() or not manifest_path.is_file():
        raise EmbeddingUnavailable(
            "embedding_assets_missing", "Pinned local embedding assets are unavailable."
        )
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise EmbeddingUnavailable(
            "embedding_manifest_invalid", "The local embedding manifest is invalid."
        ) from None
    if not isinstance(value, dict):
        raise EmbeddingUnavailable(
            "embedding_manifest_invalid", "The local embedding manifest is invalid."
        )
    model_id = value.get("model_id")
    revision = value.get("revision")
    dimensions = value.get("dimensions")
    model_file = value.get("model_file")
    file_hashes = value.get("sha256")
    expected_dim = 384 if settings.embedding_model == _LEGACY_MODEL else 512
    if (
        model_id != settings.embedding_model
        or not isinstance(revision, str)
        or not revision
        or (settings.embedding_revision and revision != settings.embedding_revision)
        or dimensions != settings.embedding_dimension
        or dimensions != expected_dim
        or not isinstance(model_file, str)
        or not isinstance(file_hashes, dict)
        or not file_hashes
        or model_file not in file_hashes
    ):
        raise EmbeddingUnavailable(
            "embedding_manifest_mismatch",
            "The local embedding manifest does not match the configured model.",
        )
    for relative_name, expected_hash in file_hashes.items():
        if not isinstance(relative_name, str) or not isinstance(expected_hash, str):
            raise EmbeddingUnavailable(
                "embedding_manifest_invalid", "The local embedding manifest is invalid."
            )
        file_path = (root / relative_name).resolve()
        if root not in file_path.parents or not file_path.is_file():
            raise EmbeddingUnavailable(
                "embedding_assets_missing", "A pinned local embedding file is missing."
            )
        try:
            actual_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()
        except OSError:
            raise EmbeddingUnavailable(
                "embedding_assets_missing",
                "A pinned local embedding file is unreadable.",
            ) from None
        if actual_hash != expected_hash:
            raise EmbeddingUnavailable(
                "embedding_asset_checksum_mismatch",
                "A pinned local embedding file failed its checksum check.",
            )
    model_path_file = (root / model_file).resolve()
    if root not in model_path_file.parents or not model_path_file.is_file():
        raise EmbeddingUnavailable(
            "embedding_assets_missing",
            "The pinned local embedding model file is missing.",
        )
    return value


class FastEmbedE5Adapter:
    """E5 wrapper that adds required task prefixes and validates vectors."""

    adapter_name = "fastembed-e5-custom-v1"
    adapter_type = "fastembed-local"

    def __init__(self, settings: Settings) -> None:
        model_path = settings.embedding_model_path
        if model_path is None:
            raise EmbeddingUnavailable(
                "embedding_unavailable",
                "Local embedding model files are not configured.",
            )
        manifest = _manifest(model_path, settings)
        self.model_id = str(manifest["model_id"])
        self.revision = str(manifest["revision"])
        self.dimensions = int(manifest["dimensions"])
        self._batch_size = settings.embedding_batch_size
        self._model = _load_fastembed(settings, manifest)

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        prefixed = [f"passage: {text}" for text in texts]
        return self._embed(prefixed)

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise EmbeddingUnavailable(
                "empty_query", "Embedding query cannot be empty."
            )
        vectors = self._embed([f"query: {text.strip()}"])
        return vectors[0]

    def embed_multimodal(self, items: list[dict[str, Any]]) -> list[list[float]]:
        texts = [str(item.get("text", "")) for item in items]
        return self.embed_passages(texts)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        try:
            raw_vectors = self._model.embed(texts, batch_size=self._batch_size)
            vectors = [
                _validate_vector(vector, self.dimensions) for vector in raw_vectors
            ]
        except EmbeddingUnavailable:
            raise
        except Exception:
            raise EmbeddingUnavailable(
                "embedding_inference_failed", "Local embedding inference failed."
            ) from None
        if len(vectors) != len(texts):
            raise EmbeddingUnavailable(
                "embedding_output_mismatch",
                "The embedding model returned an incomplete batch.",
            )
        return vectors


class EmbeddingGemmaAdapter:
    """EmbeddingGemma-2 wrapper with task prefixes, Matryoshka truncation, and bfloat16."""

    adapter_name = "embeddinggemma-sentence-transformers-v1"
    adapter_type = "sentence-transformers"

    def __init__(self, settings: Settings) -> None:
        self.model_id = settings.embedding_model or _DEFAULT_MODEL
        self.revision = settings.embedding_revision or "main"
        self.dimensions = settings.embedding_dimension
        self._batch_size = settings.embedding_batch_size
        self._model = _load_embeddinggemma(settings)

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        formatted: list[str] = []
        for text in texts:
            stripped = text.strip()
            if stripped.startswith("title: "):
                formatted.append(stripped)
            else:
                formatted.append(f"title: none | text: {stripped}")
        return self._embed(formatted, prompt_name=None)

    def embed_query(self, text: str) -> list[float]:
        cleaned = text.strip()
        if not cleaned:
            raise EmbeddingUnavailable(
                "empty_query", "Embedding query cannot be empty."
            )
        vectors = self._embed([cleaned], prompt_name="SearchQuery")
        return vectors[0]

    def embed_multimodal(self, items: list[dict[str, Any]]) -> list[list[float]]:
        if not items:
            return []
        inputs: list[Any] = []
        for item in items:
            text = str(item.get("text", "")).strip()
            image_bytes = item.get("image_bytes")
            if image_bytes:
                try:
                    import io
                    from PIL import Image

                    pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                    if text and not text.startswith("[Image"):
                        formatted_text = (
                            text
                            if text.startswith("title: ")
                            else f"title: none | text: {text}"
                        )
                        inputs.append({"text": formatted_text, "image": pil_img})
                    else:
                        inputs.append(pil_img)
                except Exception:
                    formatted_text = (
                        text
                        if text.startswith("title: ")
                        else f"title: none | text: {text}"
                    )
                    inputs.append(formatted_text)
            else:
                formatted_text = (
                    text
                    if text.startswith("title: ")
                    else f"title: none | text: {text}"
                )
                inputs.append(formatted_text)
        return self._embed(inputs, prompt_name=None)

    def _embed(
        self, inputs: list[Any], prompt_name: str | None = None
    ) -> list[list[float]]:
        try:
            encode_kwargs: dict[str, Any] = {
                "batch_size": self._batch_size,
                "truncate_dim": self.dimensions,
                "normalize_embeddings": True,
            }
            if prompt_name is not None:
                encode_kwargs["prompt_name"] = prompt_name
            raw_vectors = self._model.encode(inputs, **encode_kwargs)
            vectors = [
                _validate_vector(vector, self.dimensions) for vector in raw_vectors
            ]
        except EmbeddingUnavailable:
            raise
        except Exception:
            raise EmbeddingUnavailable(
                "embedding_inference_failed", "Local embedding inference failed."
            ) from None
        if len(vectors) != len(inputs):
            raise EmbeddingUnavailable(
                "embedding_output_mismatch",
                "The embedding model returned an incomplete batch.",
            )
        return vectors


def _validate_vector(vector: Any, dimensions: int) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    if not isinstance(vector, list) or len(vector) != dimensions:
        raise EmbeddingUnavailable(
            "embedding_dimension_mismatch",
            "The embedding vector has an unexpected dimension.",
        )
    try:
        values = [float(value) for value in vector]
    except (TypeError, ValueError, OverflowError):
        raise EmbeddingUnavailable(
            "embedding_invalid_vector",
            "The embedding model returned an invalid vector.",
        ) from None
    if any(not math.isfinite(value) for value in values):
        raise EmbeddingUnavailable(
            "embedding_invalid_vector",
            "The embedding model returned an invalid vector.",
        )
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or not 0.98 <= norm <= 1.02:
        raise EmbeddingUnavailable(
            "embedding_invalid_norm",
            "The embedding model returned an unnormalized vector.",
        )
    return values


def _load_fastembed(settings: Settings, manifest: dict[str, Any]) -> Any:
    # Disable both telemetry channels before loading ONNX Runtime/FastEmbed.
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("ORT_DISABLE_TELEMETRY_EVENTS", "1")
    import onnxruntime  # type: ignore[import-untyped]

    onnxruntime.disable_telemetry_events()
    from fastembed import TextEmbedding
    from fastembed.common.model_description import ModelSource, PoolingType

    model_id = str(manifest["model_id"])
    model_path = settings.embedding_model_path
    assert model_path is not None
    with _REGISTRATION_LOCK:
        if model_id not in _REGISTERED_MODELS:
            try:
                TextEmbedding.add_custom_model(
                    model=model_id,
                    pooling=PoolingType.MEAN,
                    normalization=True,
                    sources=ModelSource(hf="Xenova/multilingual-e5-small"),
                    dim=settings.embedding_dimension,
                    model_file=str(manifest["model_file"]),
                    license="mit",
                )
            except ValueError as error:
                if "already registered" not in str(error).lower():
                    raise EmbeddingUnavailable(
                        "embedding_model_registration_failed",
                        "The configured local embedding model could not be registered.",
                    ) from None
            _REGISTERED_MODELS.add(model_id)
    try:
        return TextEmbedding(
            model_name=model_id,
            specific_model_path=str(model_path),
            local_files_only=True,
            threads=settings.embedding_threads,
        )
    except Exception:
        raise EmbeddingUnavailable(
            "embedding_model_load_failed",
            "Pinned local embedding model could not be loaded.",
        ) from None


def _load_embeddinggemma(settings: Settings) -> Any:
    # Disable Hugging Face hub telemetry before loading
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    import torch
    from sentence_transformers import SentenceTransformer

    model_name_or_path = settings.embedding_model or _DEFAULT_MODEL
    if settings.embedding_model_path is not None:
        resolved = _resolved_model_path(settings.embedding_model_path)
        if resolved.exists():
            model_name_or_path = str(resolved)

    try:
        return SentenceTransformer(
            model_name_or_path,
            model_kwargs={"torch_dtype": torch.bfloat16},
            config_kwargs={"audio_config": None},
        )
    except Exception:
        raise EmbeddingUnavailable(
            "embedding_model_load_failed",
            "EmbeddingGemma model could not be loaded.",
        ) from None


def get_embedding_adapter(settings: Settings) -> EmbeddingAdapter:
    """Load the configured embedding model once per process."""
    model_id = settings.embedding_model or _DEFAULT_MODEL
    model_path = settings.embedding_model_path
    model_path_key = (
        str(_resolved_model_path(model_path)) if model_path is not None else "online"
    )
    revision = settings.embedding_revision or "default"
    key = (
        model_id,
        model_path_key,
        revision,
        settings.embedding_dimension,
        settings.embedding_threads,
    )
    with _ADAPTER_LOCK:
        adapter = _ADAPTERS.get(key)
        if adapter is None:
            if "embeddinggemma" in model_id.lower():
                adapter = EmbeddingGemmaAdapter(settings)
            elif model_id == _LEGACY_MODEL:
                if model_path is None:
                    raise EmbeddingUnavailable(
                        "embedding_unavailable",
                        "Local embedding model files are not configured.",
                    )
                adapter = FastEmbedE5Adapter(settings)
            else:
                adapter = EmbeddingGemmaAdapter(settings)
            _ADAPTERS[key] = adapter
        return adapter


def _resolved_model_path(path: Path) -> Path:
    """Resolve settings paths relative to the backend, independent of cwd."""
    if path.is_absolute():
        return path.resolve()
    backend_relative = Path(__file__).resolve().parents[2] / path
    if backend_relative.exists():
        return backend_relative.resolve()
    return path.resolve()
