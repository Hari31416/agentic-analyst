"""Fetch pinned public originals, or verify local bytes without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "evals/fixtures/real-v1"
MAX_BYTES = 64 * 1024 * 1024


def verify(path: Path, entry: dict) -> None:
    if path.stat().st_size != entry["bytes"]:
        raise ValueError(f"Size mismatch: {path.name}")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != entry["sha256"]:
        raise ValueError(f"Hash mismatch: {path.name}; upstream bytes changed")


def fetch(entry: dict, destination: Path) -> None:
    # Stage bytes before replacing any verified local original.
    with tempfile.TemporaryDirectory(dir=destination.parent) as staging:
        downloaded = Path(staging) / "download"
        with urlopen(entry["url"], timeout=90) as response:
            with downloaded.open("wb") as stream:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("Download exceeds the 64 MiB bound")
                    stream.write(chunk)
        member = entry.get("archive_member")
        if member:
            candidate = Path(staging) / "original"
            with ZipFile(downloaded) as archive:
                info = archive.getinfo(member)
                if info.file_size != entry["bytes"] or info.file_size > MAX_BYTES:
                    raise ValueError("Unexpected archive member size")
                # Read the named member only; never extract archive paths.
                candidate.write_bytes(archive.read(info))
        else:
            candidate = downloaded
        verify(candidate, entry)
        candidate.replace(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="No network calls")
    args = parser.parse_args()
    entries = json.loads((FIXTURES / "downloads.json").read_text())["files"]
    raw = FIXTURES / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        name = entry["name"]
        if Path(name).name != name:
            raise ValueError("Manifest contains an unsafe filename")
        destination = raw / name
        if destination.exists():
            verify(destination, entry)
        elif args.verify:
            raise FileNotFoundError(destination)
        else:
            fetch(entry, destination)
        print(f"Verified {name}: {entry['bytes']} bytes")


if __name__ == "__main__":
    main()
