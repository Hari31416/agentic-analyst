"""Start the pinned service with only its own configuration and host runtime paths."""

import os
from pathlib import Path
import sys

from dotenv import dotenv_values

root = Path(__file__).resolve().parent.parent
values = dotenv_values(root / ".env")
image = values.get("SANDBOX_IMAGE")
if not image:
    raise SystemExit("Set SANDBOX_IMAGE in .env before starting the service")
environment = {
    key: value
    for key, value in os.environ.items()
    if key in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
}
environment.update(
    SANDBOX_HOST=values.get("SANDBOX_BIND_HOST") or "127.0.0.1",
    SANDBOX_PORT="8787",
    SANDBOX_DATA_DIR=str(root / ".sandbox" / "data"),
    SANDBOX_DEFAULT_BACKEND="microsandbox",
    SANDBOX_DEFAULT_IMAGE=image,
)
if values.get("SANDBOX_AUTH_TOKEN"):
    environment["SANDBOX_AUTH_TOKEN"] = values["SANDBOX_AUTH_TOKEN"]
os.chdir(root)
os.execve(
    sys.executable,
    [
        sys.executable,
        "-m",
        "uvicorn",
        "sandbox_service.main:app",
        "--host",
        environment["SANDBOX_HOST"],
        "--port",
        "8787",
    ],
    environment,
)
