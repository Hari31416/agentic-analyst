from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.retrieval.embedding import (
    EmbeddingUnavailable,
    FastEmbedE5Adapter,
    _manifest,
    _validate_vector,
)
from app.retrieval.service import (
    RetrievalError,
    _bound_passages,
    _clean_query,
    _dedupe_adjacent,
    _fuse,
    _source_version_predicate,
)


def test_clean_query_applies_nfkc_without_losing_indic_text() -> None:
    assert _clean_query("  आय अधिकतम   २००००० रुपये  ") == "आय अधिकतम २००००० रुपये"
    assert _clean_query("Ａctive scheme") == "Active scheme"


def test_clean_query_rejects_empty_and_oversized_queries() -> None:
    with pytest.raises(RetrievalError) as empty:
        _clean_query(" \n ")
    assert empty.value.code == "empty_query"
    with pytest.raises(RetrievalError) as oversized:
        _clean_query("x" * 2_001)
    assert oversized.value.code == "query_too_long"


def test_source_scope_rejects_invalid_versions_and_empty_scope() -> None:
    assert _source_version_predicate({}) is False
    with pytest.raises(RetrievalError) as invalid:
        _source_version_predicate({"source-1": True})
    assert invalid.value.code == "invalid_source_versions"


def test_rrf_fuses_separate_candidate_lists_with_deterministic_ties() -> None:
    common = {"document_id": "doc", "source_id": "source", "source_version": 1}
    lexical = [
        {**common, "chunk_id": "a", "lexical_rank": 1, "lexical_score": 0.8},
        {**common, "chunk_id": "b", "lexical_rank": 2, "lexical_score": 0.4},
    ]
    dense = [
        {**common, "chunk_id": "b", "dense_rank": 1, "dense_score": 0.9},
        {**common, "chunk_id": "a", "dense_rank": 2, "dense_score": 0.8},
    ]
    fused = _fuse(lexical, dense)
    assert [row["chunk_id"] for row in fused] == ["a", "b"]
    assert fused[0]["lexical_rank"] == 1
    assert fused[0]["dense_rank"] == 2
    assert fused[0]["rrf_score"] == pytest.approx(1 / 61 + 1 / 62)


def test_adjacent_overlapping_chunks_are_deduplicated_but_distinct_chunks_remain() -> (
    None
):
    overlap = "repeated eligibility terms " * 8
    first = SimpleNamespace(
        id="chunk-1",
        document_id="doc",
        ordinal=0,
        previous_id=None,
        next_id="chunk-2",
        text="first section " + overlap,
    )
    second = SimpleNamespace(
        id="chunk-2",
        document_id="doc",
        ordinal=1,
        previous_id="chunk-1",
        next_id="chunk-3",
        text=overlap + " second section",
    )
    third = SimpleNamespace(
        id="chunk-3",
        document_id="doc",
        ordinal=2,
        previous_id="chunk-2",
        next_id=None,
        text="a different page and distinct terms",
    )
    candidates = [
        {"chunk_id": chunk.id, "chunk": chunk} for chunk in (first, second, third)
    ]
    deduped = _dedupe_adjacent(candidates)
    assert [item["chunk_id"] for item in deduped] == ["chunk-1", "chunk-3"]
    assert [item["rank"] for item in deduped] == [1, 2]


def test_passage_text_budget_is_applied_to_each_returned_neighbor() -> None:
    bounded = _bound_passages(
        [
            {"chunk_id": "a", "excerpt": "x" * 100},
            {"chunk_id": "b", "excerpt": "y" * 100},
        ],
        40,
    )
    assert [len(item["excerpt"]) for item in bounded] == [20, 20]
    assert all(item["truncated"] for item in bounded)


def test_embedding_vectors_require_384_finite_unit_norm_values() -> None:
    assert _validate_vector([1.0] + [0.0] * 383, 384)[0] == 1.0
    with pytest.raises(EmbeddingUnavailable) as short:
        _validate_vector([1.0, 0.0], 384)
    assert short.value.code == "embedding_dimension_mismatch"
    with pytest.raises(EmbeddingUnavailable) as not_finite:
        _validate_vector([float("nan")] + [0.0] * 383, 384)
    assert not_finite.value.code == "embedding_invalid_vector"
    with pytest.raises(EmbeddingUnavailable) as unnormalized:
        _validate_vector([0.1] + [0.0] * 383, 384)
    assert unnormalized.value.code == "embedding_invalid_norm"


def test_local_model_manifest_is_pinned_and_checks_every_file(tmp_path: Path) -> None:
    model_file = tmp_path / "onnx" / "model_quantized.onnx"
    model_file.parent.mkdir()
    model_file.write_bytes(b"test-onnx")
    tokenizer = tmp_path / "tokenizer.json"
    tokenizer.write_text("{}", encoding="utf-8")
    manifest = {
        "model_id": "intfloat/multilingual-e5-small",
        "revision": "rev-test",
        "dimensions": 384,
        "model_file": "onnx/model_quantized.onnx",
        "sha256": {
            "onnx/model_quantized.onnx": hashlib.sha256(b"test-onnx").hexdigest(),
            "tokenizer.json": hashlib.sha256(b"{}").hexdigest(),
        },
    }
    (tmp_path / "analyst-model.json").write_text(json.dumps(manifest))
    settings = Settings(
        embedding_model=manifest["model_id"],
        embedding_model_path=tmp_path,
        embedding_dimension=384,
        embedding_revision="rev-test",
    )
    assert _manifest(tmp_path, settings)["revision"] == "rev-test"

    tokenizer.write_text("tampered", encoding="utf-8")
    with pytest.raises(EmbeddingUnavailable) as invalid:
        _manifest(tmp_path, settings)
    assert invalid.value.code == "embedding_asset_checksum_mismatch"


def test_custom_adapter_prefixes_e5_query_and_passages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_file = tmp_path / "onnx" / "model_quantized.onnx"
    model_file.parent.mkdir()
    model_file.write_bytes(b"model")
    manifest = {
        "model_id": "intfloat/multilingual-e5-small",
        "revision": "rev-test",
        "dimensions": 384,
        "model_file": "onnx/model_quantized.onnx",
        "sha256": {"onnx/model_quantized.onnx": hashlib.sha256(b"model").hexdigest()},
    }
    (tmp_path / "analyst-model.json").write_text(json.dumps(manifest))
    seen: list[list[str]] = []

    class FakeModel:
        def embed(self, texts: list[str], batch_size: int) -> list[list[float]]:
            del batch_size
            seen.append(list(texts))
            return [[1.0] + [0.0] * 383 for _ in texts]

    import app.retrieval.embedding as module

    monkeypatch.setattr(module, "_load_fastembed", lambda *_args: FakeModel())
    adapter = FastEmbedE5Adapter(
        Settings(
            embedding_model=manifest["model_id"],
            embedding_model_path=tmp_path,
            embedding_dimension=384,
            embedding_revision="rev-test",
        )
    )
    adapter.embed_passages(["नियम लागू होता है", "eligibility threshold"])
    adapter.embed_query("आय सीमा क्या है?")
    assert seen == [
        ["passage: नियम लागू होता है", "passage: eligibility threshold"],
        ["query: आय सीमा क्या है?"],
    ]


def test_embeddinggemma_adapter_prefixes_and_512_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.retrieval.embedding import EmbeddingGemmaAdapter, get_embedding_adapter

    seen_calls: list[tuple[list[str], dict[str, Any]]] = []

    class FakeSentenceTransformer:
        def encode(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            seen_calls.append((list(texts), dict(kwargs)))
            dim = kwargs.get("truncate_dim", 512)
            return [[1.0] + [0.0] * (dim - 1) for _ in texts]

    import app.retrieval.embedding as module

    module._ADAPTERS.clear()
    monkeypatch.setattr(
        module, "_load_embeddinggemma", lambda *_args: FakeSentenceTransformer()
    )

    settings = Settings(
        embedding_model="google/embeddinggemma-2",
        embedding_dimension=512,
    )
    adapter = get_embedding_adapter(settings)
    assert isinstance(adapter, EmbeddingGemmaAdapter)
    assert adapter.dimensions == 512

    passages = ["raw chunk content", "title: Summary | text: already titled"]
    vectors = adapter.embed_passages(passages)
    assert len(vectors) == 2
    assert len(vectors[0]) == 512
    assert len(vectors[1]) == 512
    assert seen_calls[0][0] == [
        "title: none | text: raw chunk content",
        "title: Summary | text: already titled",
    ]
    assert seen_calls[0][1]["truncate_dim"] == 512
    assert seen_calls[0][1]["normalize_embeddings"] is True
    assert "prompt_name" not in seen_calls[0][1]

    query_vec = adapter.embed_query("search query")
    assert len(query_vec) == 512
    assert seen_calls[1][0] == ["search query"]
    assert seen_calls[1][1]["prompt_name"] == "SearchQuery"
    assert seen_calls[1][1]["truncate_dim"] == 512
    assert seen_calls[1][1]["normalize_embeddings"] is True


def test_embeddinggemma_adapter_multimodal_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image
    from app.retrieval.embedding import EmbeddingGemmaAdapter

    seen_inputs: list[list[Any]] = []

    class FakeMultimodalTransformer:
        def encode(self, inputs: list[Any], **kwargs: Any) -> list[list[float]]:
            seen_inputs.append(list(inputs))
            dim = kwargs.get("truncate_dim", 512)
            return [[1.0] + [0.0] * (dim - 1) for _ in inputs]

    import app.retrieval.embedding as module

    monkeypatch.setattr(
        module, "_load_embeddinggemma", lambda *_args: FakeMultimodalTransformer()
    )

    settings = Settings(
        embedding_model="google/embeddinggemma-2",
        embedding_dimension=512,
    )
    adapter = EmbeddingGemmaAdapter(settings)

    img = Image.new("RGB", (32, 32), color=(0, 255, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_png = buf.getvalue()

    items = [
        {"text": "[Image: chart.png]", "image_bytes": raw_png},
        {"text": "Revenue grew 20% in Q3", "image_bytes": raw_png},
        {"text": "Pure text passage without image"},
    ]
    vectors = adapter.embed_multimodal(items)
    assert len(vectors) == 3
    assert all(len(v) == 512 for v in vectors)

    assert len(seen_inputs) == 1
    encoded = seen_inputs[0]
    assert len(encoded) == 3
    assert isinstance(encoded[0], Image.Image)
    assert isinstance(encoded[1], dict)
    assert encoded[1]["text"] == "title: none | text: Revenue grew 20% in Q3"
    assert isinstance(encoded[1]["image"], Image.Image)
    assert encoded[2] == "title: none | text: Pure text passage without image"


@pytest.fixture
def gemma_loader(monkeypatch):
    import sys
    import app.retrieval.embedding as module

    loaded = []

    def load(path, **kwargs):
        model = SimpleNamespace()
        loaded.append((path, kwargs, model))
        return model

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(bfloat16="bf16"))
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=load)
    )
    return module, loaded


def test_gemma_loader_resolves_configured_revision(gemma_loader, monkeypatch, tmp_path):
    import huggingface_hub

    module, loaded = gemma_loader
    snapshot = tmp_path / "snapshots" / ("a" * 40)
    seen = []

    def download(repo_id, *, revision):
        seen.append((repo_id, revision))
        return str(snapshot)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    adapter = module.EmbeddingGemmaAdapter(
        Settings(_env_file=None, embedding_revision="release-v2")
    )
    assert seen == [("google/embeddinggemma-2", "release-v2")]
    assert loaded[0][0] == str(snapshot)
    assert adapter.revision == "a" * 40


def test_gemma_local_revision_changes_with_weights(gemma_loader, tmp_path):
    module, loaded = gemma_loader
    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"first weights")
    cfg = Settings(_env_file=None, embedding_model_path=tmp_path)
    first = module.EmbeddingGemmaAdapter(cfg)
    weights.write_bytes(b"replacement weights")
    second = module.EmbeddingGemmaAdapter(cfg)
    assert first.revision.startswith("sha256:")
    assert first.revision != second.revision
    assert all(item[0] == str(tmp_path) for item in loaded)


def test_gemma_missing_local_directory_does_not_download(
    gemma_loader, monkeypatch, tmp_path
):
    import huggingface_hub

    module, loaded = gemma_loader

    def unexpected_download(*args, **kwargs):
        pytest.fail("Missing local assets must not silently download a model")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", unexpected_download)
    with pytest.raises(EmbeddingUnavailable, match="could not be loaded"):
        module.EmbeddingGemmaAdapter(
            Settings(_env_file=None, embedding_model_path=tmp_path / "missing")
        )
    assert loaded == []
