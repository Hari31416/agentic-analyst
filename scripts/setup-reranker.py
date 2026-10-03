"""Provision pinned public Jina multilingual assets for optional local reranking.

The upstream model license is CC-BY-NC-4.0. Production deployments must select
assets whose terms permit their intended use. Runtime inference never downloads.
"""

import hashlib
import json
import os
from pathlib import Path

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
MODEL = "jinaai/jina-reranker-v2-base-multilingual"
REVISION = "9cfeff2df7d40d1b78e75e5e9cebec92a99813c9"
FILES = [
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "onnx/model_quantized.onnx",
]


def main() -> None:
    directory = ROOT / "data/models/jina-reranker-v2"
    snapshot_download(
        MODEL, revision=REVISION, allow_patterns=FILES, local_dir=directory
    )
    # FastEmbed's registry uses model.onnx; pin and describe the quantized bytes.
    (directory / "onnx/model_quantized.onnx").replace(directory / "onnx/model.onnx")
    runtime_files = [
        name if name != "onnx/model_quantized.onnx" else "onnx/model.onnx"
        for name in FILES
    ]
    manifest = {
        "model_id": MODEL,
        "repo_id": MODEL,
        "revision": REVISION,
        "model_file": "onnx/model.onnx",
        "upstream_model_file": "onnx/model_quantized.onnx",
        "license": "CC-BY-NC-4.0",
        "quantization": "int8",
        "sha256": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in runtime_files
        },
    }
    (directory / "analyst-reranker-model.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    env = ROOT / ".env"
    fields = {
        "RERANKER_MODEL": MODEL,
        "RERANKER_MODEL_PATH": "../data/models/jina-reranker-v2",
        "RERANKER_REVISION": REVISION,
    }
    lines = env.read_text().splitlines() if env.exists() else []
    lines = [line for line in lines if line.split("=", 1)[0] not in fields]
    env.write_text(
        "\n".join([*lines, *[f"{key}={value}" for key, value in fields.items()]]) + "\n"
    )
    print("Pinned quantized multilingual reranker ready for optional local inference.")


if __name__ == "__main__":
    main()
