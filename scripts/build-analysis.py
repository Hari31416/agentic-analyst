"""Pull the published microVM image, or build and publish a new version."""

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = "hari31416/agentic-rag-analyst-runtime:py312-v1"


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def set_image(path: Path, image: str) -> None:
    source = path if path.exists() else ROOT / ".env.example"
    lines = source.read_text().splitlines()
    lines = [line for line in lines if not line.startswith("SANDBOX_IMAGE=")]
    path.write_text("\n".join([*lines, f"SANDBOX_IMAGE={image}"]) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="build both platforms and push to Docker Hub",
    )
    parser.add_argument("--tag", default=TAG, help="Docker Hub tag for --publish")
    args = parser.parse_args()
    example = ROOT / ".env.example"
    if args.publish:
        with tempfile.TemporaryDirectory(prefix="analysis-image-") as directory:
            metadata = Path(directory) / "metadata.json"
            run(
                "docker",
                "buildx",
                "build",
                "--platform",
                "linux/amd64,linux/arm64",
                "--provenance=false",
                "-f",
                "infra/analysis.Dockerfile",
                "-t",
                args.tag,
                "--metadata-file",
                str(metadata),
                "--push",
                "infra",
            )
            digest = json.loads(metadata.read_text())["containerimage.digest"]
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise ValueError("Docker returned an invalid image digest")
        image = f"{args.tag}@{digest}"
    else:
        image = next(
            line.split("=", 1)[1]
            for line in example.read_text().splitlines()
            if line.startswith("SANDBOX_IMAGE=")
        )
    if not re.fullmatch(r"[^\s@]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("SANDBOX_IMAGE must be an immutable registry reference")
    run("msb", "pull", image)
    if args.publish:
        set_image(example, image)
    set_image(ROOT / ".env", image)
    print(f"Analysis image ready: {image}. Restart host apps to use it.")


if __name__ == "__main__":
    main()
