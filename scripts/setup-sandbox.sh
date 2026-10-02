#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v msb >/dev/null || { echo 'Install the microsandbox CLI (msb) before starting the microVM service.' >&2; exit 1; }
uv venv --python 3.12 .sandbox/venv
uv pip install --python .sandbox/venv/bin/python 'git+https://github.com/hari31416/sandbox.git@b3f032b6a0ce1fab7cebc75250073f58b5b6c63c'
echo 'Installed pinned service. Start with .sandbox/venv/bin/python scripts/run-sandbox.py'
