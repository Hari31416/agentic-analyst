from __future__ import annotations

import hashlib
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
