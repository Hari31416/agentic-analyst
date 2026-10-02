"""One phase milestone proving accepted PDF cells execute through real microVM SQL."""

import csv
import io
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.models import Dataset
from app.sources.document_tables import accept_document_table
from app.sources.documents import process_document
from app.storage.filesystem import FileStorage
from app.tools.file_sql import query_program, table_alias
from app.tools.python import PythonExecution
from tests.test_documents import _add_document, _database, _settings
from tests.test_sandbox import _live_client

pytestmark = [pytest.mark.live, pytest.mark.live_sandbox]


async def test_accepted_pdf_table_calculates_in_real_microvm(tmp_path: Path) -> None:
    if os.getenv("LIVE_SANDBOX_ENABLED") != "1":
        pytest.skip("set LIVE_SANDBOX_ENABLED=1 for the accepted-table microVM check")
    client = _live_client()
    assert client is not None
    storage = FileStorage(tmp_path)
    sessions, session = _database()
    session.close()
    original = (
        Path(__file__).resolve().parents[2] / "evals/fixtures/v2/wide-table.pdf"
    ).read_bytes()
    document_id = _add_document(sessions, storage, original)
    settings = _settings(tmp_path).model_copy(update={"ingestion_profile": "layout"})
    assert process_document(document_id, settings, sessions)["state"] == "ready"
    with sessions() as session:
        accepted = accept_document_table(session, storage, document_id, "table-0")
    assert accepted is not None
    dataset_id = accepted["dataset_id"]
    with sessions() as session:
        dataset = session.get(Dataset, dataset_id)
        assert dataset is not None and dataset.storage_key
        content = storage.read(dataset.storage_key)
    execution = PythonExecution(client, storage, str(uuid4()), str(uuid4()))
    try:
        guest = await execution._ensure_session()
        await client.write(guest.id, f"inputs/{dataset_id}.csv", content)
        sql = f'SELECT COUNT(*) AS count, SUM(CAST(grant_inr AS DECIMAL(14,2))) AS total FROM "{table_alias(dataset_id)}"'
        result = await execution.execute(
            query_program(sql, [dataset_id], 1, 10),
            str(uuid4()),
            ["result.csv"],
            timeout_seconds=30,
        )
        assert result.status == "ok", result.data.get("stderr")
        artifact = next(
            item
            for item in result.data["artifacts"]
            if item["display_name"] == "result.csv"
        )
        rows = list(
            csv.DictReader(io.StringIO(storage.read(artifact["storage_key"]).decode()))
        )
        assert rows == [{"count": "14", "total": "525000.00"}]
    finally:
        await execution.aclose()
        await client.aclose()
