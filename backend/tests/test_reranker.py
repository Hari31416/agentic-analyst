from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.config import Settings
from app.retrieval.reranker import (
    FastEmbedCrossEncoderAdapter,
    RerankerUnavailable,
    _manifest,
    get_reranker_adapter,
)

MODEL_ID = "jinaai/jina-reranker-v2-base-multilingual"


def _write_manifest(root: Path, model: str = MODEL_ID) -> dict[str, str]:
    model_file = root / "onnx" / "model.onnx"
    model_file.parent.mkdir(parents=True)
    model_file.write_bytes(b"onnx-model")
    tokenizer = root / "tokenizer.json"
    tokenizer.write_text("{}", encoding="utf-8")
    manifest = {
        "model_id": model,
        "revision": "rev-test",
        "model_file": "onnx/model.onnx",
        "sha256": {
            "onnx/model.onnx": hashlib.sha256(b"onnx-model").hexdigest(),
            "tokenizer.json": hashlib.sha256(b"{}").hexdigest(),
        },
    }
    (root / "analyst-reranker-model.json").write_text(json.dumps(manifest))
    return manifest


def _settings(root: Path, revision: str | None = "rev-test") -> Settings:
    return Settings(
        reranker_model=MODEL_ID,
        reranker_model_path=root,
        reranker_revision=revision,
    )


def test_local_reranker_manifest_checks_all_pinned_files(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path)
    assert _manifest(tmp_path, _settings(tmp_path)) == manifest

    (tmp_path / "tokenizer.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(RerankerUnavailable) as invalid:
        _manifest(tmp_path, _settings(tmp_path))
    assert invalid.value.code == "reranker_asset_checksum_mismatch"


def test_adapter_calls_neural_cross_encoder_and_validates_scores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_manifest(tmp_path)
    seen: list[tuple[str, list[str], int]] = []

    class FakeCrossEncoder:
        def rerank(
            self, query: str, passages: list[str], batch_size: int
        ) -> list[float]:
            seen.append((query, list(passages), batch_size))
            return [0.25, -0.5]

    import app.retrieval.reranker as module

    monkeypatch.setattr(module, "_load_fastembed", lambda *_args: FakeCrossEncoder())
    adapter = FastEmbedCrossEncoderAdapter(_settings(tmp_path))
    passages = ["नियम लागू होता है", "Eligibility threshold applies"]
    assert adapter.score("आय सीमा क्या है?", passages) == [0.25, -0.5]
    assert seen == [("आय सीमा क्या है?", passages, 8)]

    class BadCountCrossEncoder:
        def rerank(self, *_args: object, **_kwargs: object) -> list[float]:
            return [1.0]

    adapter._model = BadCountCrossEncoder()
    with pytest.raises(RerankerUnavailable) as mismatch:
        adapter.score("query", passages)
    assert mismatch.value.code == "reranker_output_mismatch"

    class NonFiniteCrossEncoder:
        def rerank(self, *_args: object, **_kwargs: object) -> list[float]:
            return [float("nan"), 0.5]

    adapter._model = NonFiniteCrossEncoder()
    with pytest.raises(RerankerUnavailable) as non_finite:
        adapter.score("query", passages)
    assert non_finite.value.code == "reranker_invalid_score"


def test_reranker_rejects_unbounded_inputs_and_requires_local_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(RerankerUnavailable) as absent:
        get_reranker_adapter(
            Settings(reranker_model=MODEL_ID, reranker_model_path=tmp_path)
        )
    assert absent.value.code == "reranker_assets_missing"

    _write_manifest(tmp_path)
    import app.retrieval.reranker as module

    monkeypatch.setattr(module, "_load_fastembed", lambda *_args: object())
    adapter = FastEmbedCrossEncoderAdapter(_settings(tmp_path))
    with pytest.raises(RerankerUnavailable) as oversized:
        adapter.score("query", ["x" * 12_001])
    assert oversized.value.code == "reranker_input_too_large"

    with pytest.raises(RerankerUnavailable) as too_many:
        adapter.score("query", ["x"] * 129)
    assert too_many.value.code == "reranker_input_too_large"
