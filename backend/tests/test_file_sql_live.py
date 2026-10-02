"""Actual microVM DuckDB result and external-access gates, enabled explicitly."""

import csv
import io
import json
from uuid import uuid4

import pytest

from app.storage.filesystem import FileStorage
from app.tools.file_sql import query_program, table_alias
from app.tools.python import PythonExecution
from tests.test_sandbox import _live_client

pytestmark = [pytest.mark.live, pytest.mark.live_sandbox]


@pytest.mark.asyncio
async def test_live_duckdb_limits_full_aggregation_and_blocks_readers(tmp_path):
    import os

    if os.getenv("LIVE_SANDBOX_ENABLED") != "1":
        pytest.skip("set LIVE_SANDBOX_ENABLED=1 to run real DuckDB")
    client = _live_client()
    assert client is not None
    storage = FileStorage(tmp_path)
    execution = PythonExecution(client, storage, str(uuid4()), str(uuid4()))
    dataset = str(uuid4())
    alias = table_alias(dataset)
    try:
        guest = await execution._ensure_session()
        await client.write(
            guest.id,
            f"inputs/{dataset}.csv",
            ("id,amount\n" + "".join(f"{i:03},{i}.00\n" for i in range(100))).encode(),
        )

        async def query(sql, limit):
            result = await execution.execute(
                query_program(sql, [dataset], limit, 10),
                str(uuid4()),
                ["result.csv", "query-result.json"],
                timeout_seconds=30,
            )
            assert result.status == "ok", result.data.get("stderr")
            outputs = {
                row["display_name"]: storage.read(row["storage_key"])
                for row in result.data["artifacts"]
            }
            return json.loads(outputs["query-result.json"]), list(
                csv.DictReader(io.StringIO(outputs["result.csv"].decode()))
            )

        metadata, rows = await query(
            f'SELECT COUNT(*) AS count, SUM(CAST(amount AS DECIMAL(12,2))) AS total FROM "{alias}"',
            1,
        )
        assert rows == [{"count": "100", "total": "4950.00"}]
        assert metadata["row_count"] == 1 and not metadata["truncated"]
        metadata, rows = await query(f'SELECT id FROM "{alias}" ORDER BY id', 3)
        assert [row["id"] for row in rows] == ["000", "001", "002"]
        assert metadata["row_count"] == 3 and metadata["truncated"]
        metadata, rows = await query(f'SELECT id FROM "{alias}" WHERE false', 3)
        assert metadata["row_count"] == 0 and rows == []
        denied = await execution.execute(
            query_program("SELECT * FROM read_csv('/etc/passwd')", [dataset], 3, 10),
            str(uuid4()),
            [],
            timeout_seconds=30,
        )
        assert denied.status == "failed"
        assert "disabled" in denied.data["stderr"].lower()
    finally:
        await execution.aclose()
        await client.aclose()
