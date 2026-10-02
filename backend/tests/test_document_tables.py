from __future__ import annotations

from pathlib import Path
from typing import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
import pytest

from app.api.document_tables import router
from app.db.models import Base, Dataset, Document, DocumentBlock, Source, Workspace
from app.db.session import get_session
from app.storage.filesystem import FileStorage


def _sessions() -> tuple[sessionmaker[Session], str, str, str]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    chunks_table = Base.metadata.tables["document_chunks"]
    postgres_only = [
        index
        for index in chunks_table.indexes
        if index.name and index.name.endswith("_fts")
    ]
    for index in postgres_only:
        chunks_table.indexes.remove(index)
    try:
        Base.metadata.create_all(engine)
    finally:
        chunks_table.indexes.update(postgres_only)
    sessions = sessionmaker(engine, expire_on_commit=False)
    with sessions() as session:
        workspace = Workspace(label="Tables")
        session.add(workspace)
        session.flush()
        source = Source(
            workspace_id=workspace.id,
            kind="document",
            version=1,
            display_name="report.pdf",
            state="ready",
            storage_key=f"originals/{workspace.id}/source/original.pdf",
            content_hash="a" * 64,
            details={"media_type": "application/pdf"},
        )
        session.add(source)
        session.flush()
        document = Document(
            source_id=source.id,
            source_version=1,
            extractor_version="test-extractor",
            chunker_version="test-chunker",
            state="ready",
            stage="indexed",
            progress=100,
            details={"warnings": ["OCR confidence is unverified."]},
        )
        session.add(document)
        session.flush()
        for ordinal, (row, cells) in enumerate(
            [
                (0, ["Applicant", "Amount"]),
                (1, ["A-01", "₹10,000"]),
                (2, ["A-02", "12500"]),
            ]
        ):
            session.add(
                DocumentBlock(
                    document_id=document.id,
                    ordinal=ordinal,
                    kind="table_row",
                    text=" | ".join(cells),
                    heading="Awards",
                    location={
                        "table": 0,
                        "row": row,
                        "cell_count": len(cells),
                        "cells": cells,
                        "page": 2,
                        "slide": 1,
                        "extractor": "ocr",
                        "ocr_confidence": 91.4,
                        "ocr_confidence_type": "mean_tesseract_word_confidence_percent",
                        "cell_locations": [
                            {"cell_id": f"cell-{row}-{column}", "bbox": [0, 0, 4, 4]}
                            for column in range(len(cells))
                        ],
                    },
                    language="en",
                    scripts=["Latin"],
                )
            )
        session.commit()
        return sessions, workspace.id, source.id, document.id


def _client(sessions: sessionmaker[Session]) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    def override_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    return TestClient(app)


def test_table_preview_is_bounded_typed_and_has_cell_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, _, _, document_id = _sessions()
    storage = FileStorage(tmp_path)
    monkeypatch.setattr("app.api.document_tables.get_storage", lambda _: storage)
    response = _client(sessions).get(f"/api/documents/{document_id}/tables")
    assert response.status_code == 200
    table = response.json()["tables"][0]
    assert table["table_id"] == "table-0"
    assert table["title"] == "Awards"
    assert table["slide"] == 1
    assert table["row_count"] == 2
    assert table["columns"] == [
        {"name": "Applicant", "type": "text", "nullable": False},
        {"name": "Amount", "type": "integer", "nullable": False, "unit": "INR"},
    ]
    amount = table["preview"][0]["cells"][1]
    assert amount["value"] == 10000
    assert amount["type"] == "integer"
    assert amount["provenance"]["row"] == 1
    assert amount["provenance"]["column"] == 1
    assert amount["provenance"]["page"] == 2
    assert amount["provenance"]["slide"] == 1
    assert amount["provenance"]["extractor"] == "ocr"
    assert amount["provenance"]["ocr_confidence"] == 91.4
    assert amount["provenance"]["cell_id"] == "cell-1-1"
    assert amount["provenance"]["bbox"] == [0, 0, 4, 4]
    assert amount["provenance"]["raw_value"] == "₹10,000"
    assert "OCR confidence is unverified." in table["warnings"]
    assert table["accepted_dataset_id"] is None


def test_accept_table_persists_derived_csv_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, workspace_id, source_id, document_id = _sessions()
    storage = FileStorage(tmp_path)
    original_key = f"originals/{workspace_id}/{source_id}/original.pdf"
    storage.put(original_key, b"immutable document original")
    monkeypatch.setattr("app.api.document_tables.get_storage", lambda _: storage)
    client = _client(sessions)

    first = client.post(f"/api/documents/{document_id}/tables/table-0/accept")
    assert first.status_code == 200, first.text
    accepted = first.json()
    assert accepted["designation"] == "derived"
    assert accepted["lineage"][0] == f"source:{source_id}@v1"
    assert accepted["lineage"][1] == f"document:{document_id}@v1"
    assert accepted["details"]["row_count"] == 2
    assert accepted["details"]["columns"][1]["type"] == "integer"
    assert accepted["details"]["columns"][1]["unit"] == "INR"
    accepted_bytes = storage.read(
        f"derived/{workspace_id}/{accepted['source_id']}/table-0.csv"
    )
    assert accepted_bytes == b"Applicant,Amount\nA-01,10000\nA-02,12500\n"

    second = client.post(f"/api/documents/{document_id}/tables/table-0/accept")
    assert second.status_code == 200
    assert second.json()["dataset_id"] == accepted["dataset_id"]
    assert second.json()["source_id"] == accepted["source_id"]
    assert storage.read(original_key) == b"immutable document original"
    with sessions() as session:
        document = session.get(Document, document_id)
        assert document is not None
        assert (
            document.details["accepted_tables"]["table-0"]["dataset_id"]
            == accepted["dataset_id"]
        )
        assert session.scalar(select(func.count()).select_from(Dataset)) == 1
        dataset = session.get(Dataset, accepted["dataset_id"])
        assert dataset is not None
        assert dataset.lineage[-1].startswith("sha256:")
        assert "document_cell:cell-1-0" in dataset.lineage
        assert dataset.details["cell_provenance"][0]["cell_id"] == "cell-1-0"


def test_cell_typing_preserves_identifiers_and_exact_decimal_values() -> None:
    from app.sources.document_tables import _invalid_grouping, _parse_value

    assert _parse_value("001234", "Account ID") == ("text", "001234")
    assert _parse_value("0.10", "Rate") == ("number", "0.10")
    assert _parse_value("₹1,00,000", "Amount") == ("integer", 100000)
    assert _parse_value("1,2", "Amount") == ("text", "1,2")
    assert _invalid_grouping("1,2") is True
    assert _invalid_grouping("Name, Inc") is False


def test_accept_rejects_unavailable_cells_and_nonready_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions, _, _, document_id = _sessions()
    storage = FileStorage(tmp_path)
    monkeypatch.setattr("app.api.document_tables.get_storage", lambda _: storage)
    with sessions() as session:
        block = session.scalar(select(DocumentBlock).where(DocumentBlock.ordinal == 0))
        assert block is not None
        block.location = {"table": 1, "row": 0, "cell_count": 2}
        document = session.get(Document, document_id)
        assert document is not None
        document.state = "processing"
        session.commit()
    client = _client(sessions)
    not_ready = client.post(f"/api/documents/{document_id}/tables/table-0/accept")
    assert not_ready.status_code == 409
    with sessions() as session:
        document = session.get(Document, document_id)
        assert document is not None
        document.state = "ready"
        session.commit()
    unavailable = client.post(f"/api/documents/{document_id}/tables/table-1/accept")
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"]["code"] == "table_cells_unavailable"


def test_table_header_names_remain_unique_after_suffixing_and_truncation():
    from app.sources.document_tables import _column_names

    names = _column_names(["Amount", "Amount", "Amount (2)", "x" * 260, "x" * 261], 5)
    assert len(set(names)) == 5
    assert max(map(len, names)) <= 255
