"""Download pinned public model assets for local-only multilingual inference."""

import hashlib
import json
import os
from pathlib import Path

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
MODEL = "intfloat/multilingual-e5-small"
REPO = "Xenova/multilingual-e5-small"
REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
FILES = [
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "onnx/model_quantized.onnx",
]


def main() -> None:
    directory = ROOT / "data/models/multilingual-e5-small"
    snapshot_download(
        REPO, revision=REVISION, allow_patterns=FILES, local_dir=directory
    )
    manifest = {
        "model_id": MODEL,
        "repo_id": REPO,
        "revision": REVISION,
        "dimensions": 384,
        "model_file": "onnx/model_quantized.onnx",
        "sha256": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in FILES
        },
    }
    (directory / "analyst-model.json").write_text(json.dumps(manifest, indent=2) + "\n")
    env = ROOT / ".env"
    lines = (
        env.read_text().splitlines()
        if env.exists()
        else (ROOT / ".env.example").read_text().splitlines()
    )
    fields = {
        "EMBEDDING_MODEL": MODEL,
        "EMBEDDING_MODEL_PATH": "../data/models/multilingual-e5-small",
        "EMBEDDING_DIMENSION": "384",
        "EMBEDDING_REVISION": REVISION,
    }
    lines = [line for line in lines if line.split("=", 1)[0] not in fields]
    env.write_text(
        "\n".join([*lines, *[f"{key}={value}" for key, value in fields.items()]]) + "\n"
    )
    print(
        "Pinned multilingual model assets ready. Restart host apps for local CPU inference."
    )


if __name__ == "__main__":
    main()
