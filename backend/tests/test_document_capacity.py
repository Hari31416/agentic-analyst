from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.sources.documents import DocumentIngestionError, ExtractedBlock, _make_chunks


def test_document_capacity_retains_tail_and_rejects_overflow() -> None:
    blocks = [
        ExtractedBlock(
            "paragraph",
            "content " * 50 + f"TAIL-{index}",
            f"Section {index}",
            {"page": index + 1},
            "en-IN",
            ["Latin"],
        )
        for index in range(520)
    ]
    chunks = _make_chunks(blocks, max_chunks=4096)
    assert len(chunks) > 512
    assert "TAIL-519" in chunks[-1].text
    with pytest.raises(DocumentIngestionError) as error:
        _make_chunks(blocks, max_chunks=512)
    assert error.value.code == "document_chunk_limit"
    assert "512" in str(error.value)


@pytest.mark.parametrize("limit", [0, -1, 16385])
def test_document_capacity_settings_are_bounded(limit: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, document_max_chunks=limit)


def test_document_capacity_setting_default_and_override() -> None:
    assert Settings(_env_file=None).document_max_chunks == 4096
    assert (
        Settings(_env_file=None, document_max_chunks=8192).document_max_chunks == 8192
    )
