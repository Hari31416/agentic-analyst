"""Provision pinned multilingual Whisper assets; inference never downloads them."""

import hashlib
import json
import os
from pathlib import Path

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
MODEL = "Systran/faster-whisper-tiny"
REVISION = "d90ca5fe260221311c53c58e660288d3deb8d356"
FILES = ["model.bin", "config.json", "tokenizer.json", "vocabulary.txt"]


def main() -> None:
    directory = ROOT / "data/models/faster-whisper-tiny"
    snapshot_download(
        MODEL, revision=REVISION, allow_patterns=FILES, local_dir=directory
    )
    manifest = {
        "model_id": MODEL,
        "revision": REVISION,
        "license": "MIT",
        "sha256": {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in FILES
        },
    }
    (directory / "analyst-speech-model.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    env = ROOT / ".env"
    fields = {"SPEECH_MODEL_PATH": "../data/models/faster-whisper-tiny"}
    lines = env.read_text().splitlines() if env.exists() else []
    lines = [line for line in lines if line.split("=", 1)[0] not in fields]
    env.write_text(
        "\n".join([*lines, *[f"{key}={value}" for key, value in fields.items()]]) + "\n"
    )
    print(
        "Pinned local multilingual Whisper tiny assets ready; model quality remains a baseline."
    )


if __name__ == "__main__":
    main()
