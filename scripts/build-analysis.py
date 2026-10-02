"""Build and register the local microVM analysis image, then record its immutable ref."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = "agentic-rag-analyst:analysis-v1"


def run(*args: str, capture: bool = False) -> str:
    result = subprocess.run(
        args, cwd=ROOT, check=True, text=True, capture_output=capture
    )
    return result.stdout or ""


def main() -> None:
    archive = ROOT / ".sandbox" / "analysis.tar"
    archive.parent.mkdir(exist_ok=True)
    run("docker", "build", "-f", "infra/analysis.Dockerfile", "-t", TAG, ".")
    run("docker", "save", "-o", str(archive), TAG)
    run("msb", "load", "-i", str(archive), "-t", TAG)
    metadata = json.loads(
        run("msb", "image", "inspect", TAG, "--format", "json", capture=True)
    )
    # The microsandbox manifest digest differs from Docker's image config digest.
    digest = metadata["digest"]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise ValueError("microsandbox returned an invalid image digest")
    pinned = f"{TAG}@{digest}"
    run("msb", "load", "-i", str(archive), "-t", pinned)
    env = ROOT / ".env"
    lines = (
        env.read_text().splitlines()
        if env.exists()
        else (ROOT / ".env.example").read_text().splitlines()
    )
    lines = [line for line in lines if not line.startswith("SANDBOX_IMAGE=")]
    env.write_text("\n".join([*lines, f"SANDBOX_IMAGE={pinned}"]) + "\n")
    archive.unlink()
    print(f"Analysis image registered: {pinned}. Restart host apps to use it.")


if __name__ == "__main__":
    main()
