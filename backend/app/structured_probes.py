"""Live phase 02 gates against synthetic sources through the host API and worker."""

import asyncio
import csv
import hashlib
import io
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import xlwt  # type: ignore[import-untyped]
from sqlalchemy import select

from app.probes import request, wait_terminal
from app.config import get_settings
from app.db.models import Evidence, Source, ToolCall
from app.db.session import factory
from app.storage.factory import get_storage

ROOT = Path(__file__).resolve().parents[2]


def legacy_fixture() -> bytes:
    book = xlwt.Workbook()
    sheet = book.add_sheet("Applications")
    for i, row in enumerate(
        csv.reader((ROOT / "evals/fixtures/v1/applications.csv").open())
    ):
        for j, value in enumerate(row):
            sheet.write(i, j, value)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def run_case(
    client: httpx.AsyncClient, workspace: str, kind: str, source: dict[str, Any]
) -> dict[str, Any]:
    threads = await request(client, "GET", f"/api/workspaces/{workspace}/threads")
    previous = next(
        (item for item in threads if item["label"] == f"Phase 02 {kind}"), None
    )
    thread = previous or await request(
        client,
        "POST",
        f"/api/workspaces/{workspace}/threads",
        json={"label": f"Phase 02 {kind}"},
    )
    prior_runs = await request(client, "GET", f"/api/threads/{thread['id']}/runs")
    completed = next(
        (
            item
            for item in reversed(prior_runs)
            if item["state"] == "completed"
            and item["selected_source_ids"] == [source["id"]]
        ),
        None,
    )

    language = "hi-IN" if kind == "xlsx" else "en-IN"
    run = completed or await request(
        client,
        "POST",
        f"/api/threads/{thread['id']}/runs",
        json={
            "text": "Inspect the selected source and its schema with tools. For scheme S1, active status, annual_income_inr <= 200000.00, calculate eligible count and sum grant_amount_inr. Use run_sql on the full dataset, with output columns eligible_count and total_inr. Cast file VARCHAR amounts to DECIMAL explicitly. Retain the result CSV, report INR and the threshold, and cite actual calculation evidence and artifacts. Do not substitute a sample or hardcode the answer.",
            "selected_source_ids": [source["id"]],
            "answer_language": language,
        },
    )
    finished = await wait_terminal(client, run["id"])
    if finished["state"] != "completed":
        raise AssertionError(f"{kind}: {finished['state']} {finished.get('outcome')}")
    artifacts = await request(client, "GET", f"/api/runs/{run['id']}/artifacts")
    result = next(row for row in artifacts if row["display_name"] == "result.csv")
    content = await client.get(f"/api/artifacts/{result['id']}/content")
    content.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(content.text)))
    assert (
        len(rows) == 1
        and Decimal(rows[0]["eligible_count"]) == 2
        and Decimal(rows[0]["total_inr"]) == 25000
    ), rows
    assert hashlib.sha256(content.content).hexdigest() == result["sha256"]
    assert (
        finished["outcome"]["evidence_ids"]
        and result["id"] in finished["outcome"]["artifact_ids"]
    )
    with factory()() as session:
        evidence = session.get(Evidence, finished["outcome"]["evidence_ids"][0])
        assert (
            evidence
            and evidence.details["source_versions"][source["id"]] == source["version"]
        )
        assert evidence.details["result_sha256"] == result["sha256"]
        tools = list(
            session.scalars(select(ToolCall).where(ToolCall.run_id == run["id"]))
        )
        assert any(
            tool.name == "run_sql" and tool.status == "completed" for tool in tools
        )
    return {
        "kind": kind,
        "source_id": source["id"],
        "thread_id": thread["id"],
        "run_id": run["id"],
        "result_artifact_id": result["id"],
        "result_sha256": result["sha256"],
        "eligible_count": 2,
        "total_inr": "25000.00",
        "language": language,
        "evidence_ids": finished["outcome"]["evidence_ids"],
        "cleanup": finished["outcome"].get("cleanup"),
        "tools": [tool.name for tool in tools],
    }


async def main() -> None:
    settings = get_settings()
    storage = get_storage(settings)
    report: dict[str, Any] = {
        "phase": "02",
        "timestamp": datetime.now(UTC).isoformat(),
        "model": settings.openai_model,
        "sandbox_image": settings.sandbox_image,
        "cases": [],
    }
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", timeout=90
    ) as client:
        workspace = (
            {"id": os.environ["PHASE02_WORKSPACE_ID"]}
            if os.getenv("PHASE02_WORKSPACE_ID")
            else await request(
                client,
                "POST",
                "/api/workspaces",
                json={"label": "Phase 02 live synthetic gates"},
            )
        )
        originals = []
        for kind in ("csv", "xlsx", "xls"):
            content = (
                legacy_fixture()
                if kind == "xls"
                else (ROOT / f"evals/fixtures/v1/applications.{kind}").read_bytes()
            )
            existing_sources = await request(
                client, "GET", f"/api/workspaces/{workspace['id']}/sources"
            )
            source = next(
                (row for row in existing_sources if row["kind"] == kind), None
            ) or await request(
                client,
                "POST",
                f"/api/workspaces/{workspace['id']}/sources/files",
                files={"file": (f"applications.{kind}", content)},
            )
            originals.append((source["id"], hashlib.sha256(content).hexdigest()))
            report["cases"].append(
                await run_case(client, workspace["id"], kind, source)
            )
            print(f"{kind}: live count 2, INR 25000 and evidence verified", flush=True)
        for kind, port, user, password in (
            ("postgresql", 55432, "analyst", "analyst"),
            ("mysql", 33306, "eval_analyst", "eval-analyst-dev"),
        ):
            response = await request(
                client,
                "POST",
                f"/api/workspaces/{workspace['id']}/connections",
                json={
                    "dialect": kind,
                    "host": "127.0.0.1",
                    "port": port,
                    "database_name": "analyst_eval",
                    "username": user,
                    "password": password,
                    "options": {"ssl_mode": "disable"},
                },
            )
            report["cases"].append(
                await run_case(client, workspace["id"], kind, response["source"])
            )
            print(f"{kind}: live count 2, INR 25000 and evidence verified", flush=True)
        with factory()() as session:
            for source_id, digest in originals:
                source = session.get(Source, source_id)
                assert source and source.storage_key and source.content_hash == digest
                retained = storage.read(source.storage_key, settings.max_upload_bytes)
                assert hashlib.sha256(retained).hexdigest() == digest
        report["original_hashes_unchanged"] = True
        report["workspace_id"] = workspace["id"]
    path = ROOT / "evals/reports/phase02-2026-10-02.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Recorded {len(report['cases'])} live cases at {path}")


if __name__ == "__main__":
    asyncio.run(main())
