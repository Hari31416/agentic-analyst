"""Pinned public downloads must fail closed when bytes change."""

import hashlib
import importlib.util
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest


def loader():
    path = Path(__file__).resolve().parents[1] / "generators/download_real_v1.py"
    spec = importlib.util.spec_from_file_location("real_downloader", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entry(payload, member=None):
    return {
        "url": "https://example.invalid/public.zip",
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "archive_member": member,
    }


def test_changed_same_size_original_is_rejected(tmp_path):
    module = loader()
    original = tmp_path / "original.pdf"
    original.write_bytes(b"good")
    expected = entry(b"good")
    module.verify(original, expected)
    original.write_bytes(b"evil")
    with pytest.raises(ValueError, match="Hash mismatch"):
        module.verify(original, expected)


def test_download_mismatch_preserves_existing_file(tmp_path, monkeypatch):
    module = loader()
    destination = tmp_path / "original.pdf"
    destination.write_bytes(b"keep")
    monkeypatch.setattr(module, "urlopen", lambda *a, **kw: BytesIO(b"evil"))
    with pytest.raises(ValueError, match="Hash mismatch"):
        module.fetch(entry(b"good"), destination)
    assert destination.read_bytes() == b"keep"


def test_zip_download_reads_only_pinned_member(tmp_path, monkeypatch):
    module = loader()
    zipped = BytesIO()
    with ZipFile(zipped, "w") as archive:
        archive.writestr("Online Retail.xlsx", b"workbook")
        archive.writestr("../unwanted.txt", b"untrusted")
    monkeypatch.setattr(module, "urlopen", lambda *a, **kw: BytesIO(zipped.getvalue()))
    destination = tmp_path / "retail.xlsx"
    module.fetch(entry(b"workbook", "Online Retail.xlsx"), destination)
    assert destination.read_bytes() == b"workbook"
    assert list(tmp_path.iterdir()) == [destination]


def test_oversized_download_is_rejected_before_replace(tmp_path, monkeypatch):
    module = loader()
    monkeypatch.setattr(module, "MAX_BYTES", 4)
    monkeypatch.setattr(module, "urlopen", lambda *a, **kw: BytesIO(b"oversized"))
    destination = tmp_path / "retail.xlsx"
    with pytest.raises(ValueError, match="64 MiB bound"):
        module.fetch(entry(b"oversized"), destination)
    assert not destination.exists()
