"""Phase milestone checks in the real networkless microVM."""

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.storage.filesystem import FileStorage
from app.tools.analysis import AnalyzeInput
from app.tools.python import PythonExecution
from tests.test_sandbox import _live_client

pytestmark = [pytest.mark.live, pytest.mark.live_sandbox]


@pytest.mark.asyncio
async def test_live_analysis_join_statistics_chart_and_library_versions(tmp_path):
    if os.getenv("LIVE_SANDBOX_ENABLED") != "1":
        pytest.skip("explicit milestone opt-in required")
    client = _live_client()
    assert client is not None
    store = FileStorage(tmp_path)
    runner = PythonExecution(client, store, str(uuid4()), str(uuid4()))
    engine = Path("app/tools/analysis_engine.py").read_text()
    try:
        session = await runner._ensure_session()
        await client.write(
            session.id,
            "inputs/sales.csv",
            b"id,amount,quantity\n001,9007199254740993.01,1\n002,0.02,2\n003,1.03,3\n",
        )
        await client.write(
            session.id,
            "inputs/lookup.csv",
            b"id,region\n001,North\n002,North\n003,South\n",
        )
        config = AnalyzeInput(
            inputs=[
                {"alias": "sales", "dataset_id": uuid4()},
                {"alias": "lookup", "artifact_id": uuid4()},
            ],
            result_frame="sales",
            operations=[
                {
                    "kind": "convert",
                    "frame": "sales",
                    "columns": ["amount", "quantity"],
                    "data_type": "number",
                },
                {
                    "kind": "join",
                    "frame": "sales",
                    "columns": ["id"],
                    "right_frame": "lookup",
                    "right_on": ["id"],
                    "relationship": "one_to_one",
                },
                {
                    "kind": "statistics",
                    "frame": "sales",
                    "columns": ["quantity"],
                    "function": "describe",
                },
                {
                    "kind": "aggregate",
                    "frame": "sales",
                    "columns": ["region"],
                    "metrics": [
                        {"column": "amount", "function": "sum", "output": "total"}
                    ],
                },
            ],
            assumptions=["Amounts are INR; identifiers are source-provided strings."],
            chart={
                "kind": "bar",
                "x": "region",
                "y": "total",
                "title": "Revenue by region",
                "unit": "INR",
            },
        ).model_dump(mode="json")
        config["inputs"][0].update(path="sales.csv", units={"amount": "INR"})
        config["inputs"][1].update(path="lookup.csv", units={})
        code = (
            engine
            + "\nrun_analysis(json.loads("
            + repr(json.dumps(config))
            + "))\n"
            + "\nimport importlib.metadata\nPath('libraries.json').write_text(json.dumps({p:importlib.metadata.version(p) for p in ['pandas','numpy','scipy','matplotlib','duckdb','pyarrow']}))\n"
        )
        result = await runner.execute(
            code,
            str(uuid4()),
            [
                "result.csv",
                "analysis.json",
                "chart.json",
                "chart.png",
                "libraries.json",
            ],
            timeout_seconds=120,
        )
        outputs = {
            a["display_name"]: store.read(a["storage_key"])
            for a in result.data["artifacts"]
        }
        assert result.status == "ok", result.data
        metadata = json.loads(outputs["analysis.json"])
        assert metadata["status"] == "ok", metadata
        assert metadata["rows"] == [
            {"region": "North", "total": "9007199254740993.03"},
            {"region": "South", "total": "1.03"},
        ]
        assert metadata["operations"][1]["unmatched_left"] == 0
        assert metadata["operations"][2]["statistics"]["quantity"]["mean"] == 2
        assert metadata["units"]["total"] == "INR"
        assert outputs["chart.png"].startswith(b"\x89PNG")
        chart = json.loads(outputs["chart.json"])
        assert chart["layout"]["yaxis"]["title"] == "total (INR)"
        libraries = json.loads(outputs["libraries.json"])
        assert len(libraries) == 6
        proof = {
            "status": "passed",
            "analysis": metadata,
            "libraries": libraries,
            "artifact_hashes": {
                a["display_name"]: a["sha256"] for a in result.data["artifacts"]
            },
            "sandbox_image": client.image,
        }
        proof_path = Path("../evals/results/phase06-analysis-live.json")
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        proof_path.write_text(json.dumps(proof, ensure_ascii=False, indent=2))
    finally:
        await runner.aclose()
        await client.aclose()
