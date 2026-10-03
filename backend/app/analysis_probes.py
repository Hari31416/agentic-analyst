"""One retained phase 06 agent milestone using synthetic DB/file/document inputs."""

import asyncio
import csv
import hashlib
import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.db.models import Source, ToolCall
from app.db.session import factory
from app.probes import request, wait_terminal
from app.storage.factory import get_storage

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "evals/reports" / f"phase06-{datetime.now(UTC):%Y%m%dT%H%M%S}.json"


async def main() -> None:
    settings = get_settings()
    target = make_url(settings.database_url.get_secret_value()).set(
        database="analyst_eval"
    )
    engine = create_engine(target)
    with engine.connect() as connection:
        before = [
            list(row)
            for row in connection.execute(
                text(
                    "SELECT application_id,grant_amount_inr::text FROM public.synthetic_applications ORDER BY application_id"
                )
            )
        ]
    report: dict[str, Any] = {
        "phase": "06",
        "timestamp": datetime.now(UTC).isoformat(),
        "model": settings.openai_model,
        "sandbox_image": settings.sandbox_image,
        "cases": [],
    }

    def save() -> None:
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", timeout=90
    ) as client:
        workspace = (
            {"id": os.environ["PHASE06_WORKSPACE_ID"]}
            if os.getenv("PHASE06_WORKSPACE_ID")
            else await request(
                client,
                "POST",
                "/api/workspaces",
                json={"label": "Phase 06 mixed analysis and portable report"},
            )
        )
        report["workspace_id"] = workspace["id"]
        save()
        content = json.dumps(
            [
                {
                    "application_id": f"APP-{n:03}",
                    "region": "North" if n <= 2 else "South" if n <= 4 else "West",
                }
                for n in range(1, 6)
            ]
        ).encode()
        file = await request(
            client,
            "POST",
            f"/api/workspaces/{workspace['id']}/sources/files",
            files={"file": ("regions.json", content)},
        )
        doc_bytes = (ROOT / "evals/fixtures/v1/applications-en.pdf").read_bytes()
        doc = await request(
            client,
            "POST",
            f"/api/workspaces/{workspace['id']}/documents",
            files={"file": ("applications-en.pdf", doc_bytes)},
        )
        for _ in range(180):
            docs = await request(
                client, "GET", f"/api/workspaces/{workspace['id']}/documents"
            )
            state = next(d for d in docs if d["id"] == doc["document"]["id"])
            if state["state"] == "ready":
                break
            if state["state"] == "failed":
                raise AssertionError("Synthetic document ingestion failed")
            await asyncio.sleep(1)
        else:
            raise AssertionError("Synthetic document indexing timed out")
        database = await request(
            client,
            "POST",
            f"/api/workspaces/{workspace['id']}/connections",
            json={
                "dialect": "postgresql",
                "host": target.host,
                "port": target.port,
                "database_name": target.database,
                "username": target.username,
                "password": target.password or "",
                "options": {"ssl_mode": "disable"},
            },
        )
        thread = await request(
            client,
            "POST",
            f"/api/workspaces/{workspace['id']}/threads",
            json={"label": "Mixed analysis report"},
        )
        prompt = "For analyze_data use inputs:[{alias:grants,artifact_id:the exact returned CSV ID},{alias:regions,dataset_id:the exact listed JSON dataset ID}], result_frame:grants, and operations with kind/frame/columns, plus right_frame/right_on/relationship for the join. The operations schema uses kind, not type; filter uses comparison and value; aggregate uses metrics of column/function/output. All inputs are staged as canonical CSV. Do not pass database dataset IDs to run_python. If a validation error occurs, correct the named field rather than guessing a new input ID. Inspect the selected sources with list_sources and inspect_schema. Retrieve the eligibility rule from the policy document with search_documents. Use run_sql to fetch the application rows from the selected database with application_id, scheme_code, scheme_status, annual_income_inr and grant_amount_inr. Use the exact discovered column names, alias them if needed. This fixture contains five rows; retain a bounded CSV snapshot. Then use analyze_data with that CSV artifact plus the selected regions JSON dataset. Reject missing identifiers and duplicate identifiers, join one_to_one on application_id, explicitly convert income and grant to numbers, filter using the retrieved rule with the inclusive income boundary, calculate descriptive statistics for grants, and aggregate by region with eligible_count and total_inr. Amount units are INR. The region file defines the entity mapping. Create a labelled bar chart of total_inr by region in INR, retain chart JSON and PNG. Generate a Markdown/PDF/replay notebook report using generate_report from the final result CSV plus original document evidence and calculation evidence. Record assumptions, the read-only snapshot limitation, and association versus causation. Return the regional count and exact total with document and calculation citations and the final table/chart/report artifact IDs. Answer in English. Never hardcode totals; derive them through tools."
        prompt += f" Every database run_sql call must explicitly set source_id to {database['source']['id']}; do not omit source_id or use file SQL for database tables. "
        run = await request(
            client,
            "POST",
            f"/api/threads/{thread['id']}/runs",
            json={
                "text": prompt,
                "selected_source_ids": [
                    file["id"],
                    doc["source"]["id"],
                    database["source"]["id"],
                ],
                "answer_language": "en-IN",
            },
        )
        report["cases"].append(
            {"run_id": run["id"], "thread_id": thread["id"], "status": "running"}
        )
        save()
        finished = await wait_terminal(client, run["id"])
        case = report["cases"][0]
        case.update(
            status=finished["state"],
            state=finished["state"],
            outcome=finished["outcome"],
        )
        report["status"] = finished["state"]
        save()
        assert finished["state"] == "completed", finished
        artifacts = await request(client, "GET", f"/api/runs/{run['id']}/artifacts")
        with factory()() as session:
            tools = list(
                session.scalars(
                    select(ToolCall)
                    .where(ToolCall.run_id == run["id"])
                    .order_by(ToolCall.started_at)
                )
            )
            names = [t.name for t in tools]
            calculation = next(
                t
                for t in tools
                if t.name == "analyze_data"
                and t.result
                and t.result["status"] in {"ok", "partial"}
            )
        assert {
            "search_documents",
            "run_sql",
            "analyze_data",
            "generate_report",
        } <= set(names), names
        assert calculation.result is not None
        metadata = calculation.result["data"]["analysis"]
        assert metadata["rows"] == [
            {"region": "North", "eligible_count": 2, "total_inr": "25000.00"}
        ], metadata
        case.update(
            tools=names,
            analysis=metadata,
            artifact_ids=[a["id"] for a in artifacts],
            cleanup=finished["outcome"].get("cleanup"),
        )
        save()
        downloads = {}
        for artifact in artifacts:
            if artifact["display_name"].endswith((".pdf", ".md", ".ipynb")):
                raw = await client.get(f"/api/artifacts/{artifact['id']}/download")
                raw.raise_for_status()
                assert hashlib.sha256(raw.content).hexdigest() == artifact["sha256"]
                path = ROOT / "evals/results" / artifact["display_name"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw.content)
                downloads[artifact["display_name"]] = {
                    "id": artifact["id"],
                    "sha256": artifact["sha256"],
                    "path": str(path.relative_to(ROOT)),
                }
        case["reports"] = downloads
        save()
        final_csv = next(
            a
            for a in artifacts
            if a["display_name"] == "result.csv"
            and a["id"]
            in {item["id"] for item in calculation.result["data"]["artifacts"]}
        )
        for format in ["csv", "xlsx", "parquet"]:
            response = await client.get(
                f"/api/artifacts/{final_csv['id']}/download", params={"format": format}
            )
            response.raise_for_status()
            assert len(response.content) > 0
        chart = next(a for a in artifacts if a["display_name"] == "chart.json")
        assert (await request(client, "GET", f"/api/artifacts/{chart['id']}/chart"))[
            "data"
        ][0]["y"] == [25000.0]
        reused = await request(
            client,
            "POST",
            f"/api/artifacts/{final_csv['id']}/dataset",
            json={"display_name": "Eligible regional grants.csv"},
        )
        case["derived_dataset"] = reused
        save()
        archive = await client.get(f"/api/workspaces/{workspace['id']}/export")
        archive.raise_for_status()
        imported = await request(
            client,
            "POST",
            "/api/portability/import",
            files={"file": ("workspace.zip", archive.content, "application/zip")},
        )
        case["roundtrip"] = {
            "workspace": imported["workspace"],
            "reconnection_required": imported["reconnection_required"],
            "reindex_required": imported["reindex_required"],
            "mapped_evidence_ids": {
                old: imported["id_map"].get(old)
                for old in finished["outcome"]["evidence_ids"]
            },
        }
        store = get_storage(settings)
        with factory()() as session:
            for source_id, digest in [
                (file["id"], hashlib.sha256(content).hexdigest()),
                (doc["source"]["id"], hashlib.sha256(doc_bytes).hexdigest()),
            ]:
                source = session.get(Source, source_id)
                assert source and source.storage_key and source.content_hash == digest
                assert (
                    hashlib.sha256(
                        store.read(source.storage_key, settings.max_upload_bytes)
                    ).hexdigest()
                    == digest
                )
        with engine.connect() as connection:
            after = [
                list(row)
                for row in connection.execute(
                    text(
                        "SELECT application_id,grant_amount_inr::text FROM public.synthetic_applications ORDER BY application_id"
                    )
                )
            ]
        assert after == before
        report.update(
            original_hashes_unchanged=True,
            database_rows_unchanged=True,
            status="passed",
        )
        save()
        print(
            f"Phase 06 full agent passed: {len(names)} tools; report, exports, archive roundtrip and read-only source checks.",
            flush=True,
        )
    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
