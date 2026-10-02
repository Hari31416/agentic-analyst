"""Run API, worker, and Vite directly on the host. Ctrl-C stops all three."""

import os
from pathlib import Path
import signal
import subprocess
import time

from dotenv import dotenv_values

root = Path(__file__).resolve().parent.parent
values = dotenv_values(root / ".env")
python = root / "backend" / ".venv" / "bin" / "python"
api_port = int(values.get("API_PORT") or 8000)
frontend_port = int(values.get("FRONTEND_PORT") or 5173)
frontend_env = {
    key: value
    for key, value in os.environ.items()
    if key in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
}
frontend_env["API_PROXY_URL"] = f"http://127.0.0.1:{api_port}"
processes: list[subprocess.Popen] = []
stopping = False


def stop(*_: object) -> None:
    global stopping
    stopping = True


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
try:
    processes.append(
        subprocess.Popen(
            [
                str(python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--reload",
            ],
            cwd=root / "backend",
            start_new_session=True,
        )
    )
    processes.append(
        subprocess.Popen(
            [str(python), "-m", "app.workers.main"],
            cwd=root / "backend",
            start_new_session=True,
        )
    )
    processes.append(
        subprocess.Popen(
            [
                "pnpm",
                "dev",
                "--host",
                "127.0.0.1",
                "--port",
                str(frontend_port),
                "--strictPort",
            ],
            cwd=root / "frontend",
            env=frontend_env,
            start_new_session=True,
        )
    )
    print(
        f"Apps running directly: UI http://127.0.0.1:{frontend_port}, API http://127.0.0.1:{api_port}",
        flush=True,
    )
    while not stopping and all(process.poll() is None for process in processes):
        time.sleep(0.25)
    if not stopping:
        raise SystemExit("An app exited; stopping the remaining apps.")
finally:
    for process in processes:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 30
    for process in processes:
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
