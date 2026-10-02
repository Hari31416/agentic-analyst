"""Explicit local retrieval baseline and live bilingual mixed-source milestone."""

import asyncio
import csv
import hashlib
import io
import json
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db.models import (
    Document,
    Evidence,
    IndexGeneration,
    Source,
    ToolCall,
    AuditEvent,
)
from app.db.session import factory
from app.probes import request, wait_terminal
from app.retrieval.service import get_passage, search
from app.storage.factory import get_storage
from app.sources.connections import execute_query
from app.db.models import Connection

ROOT = Path(__file__).resolve().parents[2]


def supports_criterion(passage: dict[str, Any]) -> bool:
    excerpt = str(passage["excerpt"])
    return (
        ("Qualifies" in excerpt or "पात्र" in excerpt)
        and "S1" in excerpt
        and ("200000" in excerpt or "200,000" in excerpt)
    )


async def ready_documents(
    client: httpx.AsyncClient, workspace_id: str
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        rows = await request(client, "GET", f"/api/workspaces/{workspace_id}/documents")
        if len(rows) == 4 and all(
            row["state"] in {"ready", "failed", "ocr_needed"} for row in rows
        ):
            assert all(
                row["state"] == "ready" and row["stage"] == "indexed" for row in rows
            ), rows
            return cast(list[dict[str, Any]], rows)
        await asyncio.sleep(1)
    raise AssertionError("Document ingestion did not complete")


def retrieval_baseline(documents: list[dict[str, Any]]) -> dict[str, Any]:
    settings = get_settings()
    rows = []
    with factory()() as session:
        for document in documents:
            versions = {document["source_id"]: document["source_version"]}
            for language, query in (
                ("en", "eligibility active scheme annual income limit inclusive"),
                ("hi", "पात्र योजना सक्रिय वार्षिक आय सीमा शामिल"),
            ):
                for mode in ("text", "vector", "hybrid"):
                    result = search(
                        session,
                        query,
                        versions,
                        settings,
                        mode=mode,
                        limit=10,
                        context_budget=12000,
                    )
                    assert not result["degraded"]
                    assert all(
                        p["source_id"] == document["source_id"]
                        for p in result["passages"]
                    )
                    support_ranks = [
                        rank
                        for rank, p in enumerate(result["passages"], start=1)
                        if supports_criterion(p)
                    ]
                    rows.append(
                        {
                            "document": document["display_name"],
                            "document_id": document["id"],
                            "query_language": language,
                            "mode": mode,
                            "actual_mode": result["mode"],
                            "support_rank": min(support_ranks, default=None),
                            **{
                                f"recall_at_{k}": int(
                                    any(rank <= k for rank in support_ranks)
                                )
                                for k in (3, 5, 10)
                            },
                            "supporting_chunk_ids": [
                                p["chunk_id"]
                                for p in result["passages"]
                                if supports_criterion(p)
                            ],
                        }
                    )
                    if result["passages"]:
                        passage = get_passage(
                            session,
                            result["passages"][0]["chunk_id"],
                            versions,
                            settings,
                            neighbors=1,
                            context_budget=4000,
                        )
                        assert passage["source_id"] == document["source_id"]
        for mode in ("vector", "hybrid"):
            checks = [row for row in rows if row["mode"] == mode]
            assert all(row["recall_at_10"] for row in checks), checks
        empty = search(
            session,
            "unicorn weather policy ZX999",
            {documents[0]["source_id"]: 1},
            settings,
            mode="text",
        )
        assert empty["passages"] == []
    return {
        "scope": "one selected source version per query",
        "cases": rows,
        "recall": {
            mode: {
                f"at_{k}": sum(
                    row[f"recall_at_{k}"] for row in rows if row["mode"] == mode
                )
                / len([row for row in rows if row["mode"] == mode])
                for k in (3, 5, 10)
            }
            for mode in ("text", "vector", "hybrid")
        },
        "misses_at_3": [row for row in rows if not row["recall_at_3"]],
        "misses_at_10": [row for row in rows if not row["recall_at_10"]],
        "scope_and_no_evidence_checked": True,
        "previous_chunker_baseline": {
            "chunker": "utf8-byte-window-v1",
            "scope": "one selected source version per query",
            "dense_and_hybrid_recall_at_3": 0.75,
            "misses": [
                {
                    "document": "applications-hi.docx",
                    "query_language": language,
                    "support_rank": 7,
                }
                for language in ("en", "hi")
            ],
            "change": "Group narrative blocks and bound chunks with the pinned tokenizer.",
        },
    }


async def mixed_case(
    client: httpx.AsyncClient,
    workspace_id: str,
    document: dict[str, Any],
    source: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    thread = await request(
        client,
        "POST",
        f"/api/workspaces/{workspace_id}/threads",
        json={"label": f"Phase 03 mixed {language}"},
    )
    prompt = "Use only the selected sources. Retrieve the document's eligibility criteria for scheme S1 with search_documents and cite original passages. Apply the retrieved criteria to the full structured application dataset using run_sql. Retain a one-row result CSV with eligible_count and total_inr, and an application-level eligibility/exclusion table. Use run_python with the retained query CSV artifacts to create an eligible_grants.png chart. Explain the income boundary and why APP-003, APP-004 and APP-005 are excluded. Preserve exact decimals and cite both document and calculation evidence IDs, plus the summary CSV and PNG artifact IDs. Do not use table samples for arithmetic or invent a criterion absent from the retrieved document."
    run = await request(
        client,
        "POST",
        f"/api/threads/{thread['id']}/runs",
        json={
            "text": prompt,
            "selected_source_ids": [document["source_id"], source["id"]],
            "answer_language": language,
        },
    )
    final = await wait_terminal(client, run["id"])
    assert final["state"] == "completed", final
    artifacts = await request(client, "GET", f"/api/runs/{run['id']}/artifacts")
    summary = None
    exclusions = None
    chart = None
    for artifact in artifacts:
        if artifact["media_type"] not in {"text/csv", "image/png"}:
            continue
        response = await client.get(f"/api/artifacts/{artifact['id']}/content")
        response.raise_for_status()
        assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
        if artifact["media_type"] == "image/png":
            assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
            chart = artifact
        else:
            rows = list(csv.DictReader(io.StringIO(response.text)))
            if rows and "eligible_count" in rows[0] and "total_inr" in rows[0]:
                assert (
                    len(rows) == 1
                    and Decimal(rows[0]["eligible_count"]) == 2
                    and Decimal(rows[0]["total_inr"]) == 25000
                ), rows
                summary = artifact
            if rows and "application_id" in rows[0] and len(rows) == 5:
                flags = {
                    row["application_id"]: str(
                        row.get("eligible", row.get("eligibility", ""))
                    ).lower()
                    in {"1", "true", "eligible"}
                    for row in rows
                }
                assert flags == {f"APP-{n:03d}": n <= 2 for n in range(1, 6)}, rows
                exclusions = artifact
    assert summary and chart and exclusions, [
        item["display_name"] for item in artifacts
    ]
    assert (
        summary["id"] in final["outcome"]["artifact_ids"]
        and chart["id"] in final["outcome"]["artifact_ids"]
    )
    evidence_views = [
        await request(client, "GET", f"/api/evidence/{identity}")
        for identity in final["outcome"]["evidence_ids"]
    ]
    assert {item["kind"] for item in evidence_views} >= {"document", "structured"}
    document_evidence = next(
        item for item in evidence_views if item["kind"] == "document"
    )
    assert (
        document_evidence["document_id"] == document["id"]
        and document_evidence["document_version"] == document["source_version"]
    )
    messages = await request(client, "GET", f"/api/threads/{thread['id']}/messages")
    assert document_evidence["id"] in messages[-1]["references"]["evidence_ids"]
    reopened = await request(client, "GET", f"/api/evidence/{document_evidence['id']}")
    assert (
        reopened["excerpt"] == document_evidence["excerpt"]
        and reopened["location"] == document_evidence["location"]
    )
    with factory()() as session:
        tools = list(
            session.scalars(
                select(ToolCall)
                .where(ToolCall.run_id == run["id"])
                .order_by(ToolCall.created_at)
            )
        )
        assert {tool.name for tool in tools} >= {
            "search_documents",
            "run_sql",
            "run_python",
        }
        assert session.scalar(
            select(AuditEvent.id).where(AuditEvent.run_id == run["id"])
        )
    return {
        "run_id": run["id"],
        "thread_id": thread["id"],
        "language": language,
        "document_id": document["id"],
        "source_id": source["id"],
        "eligible_count": 2,
        "total_inr": "25000.00",
        "summary_artifact_id": summary["id"],
        "exclusions_artifact_id": exclusions["id"],
        "chart_artifact_id": chart["id"],
        "evidence_ids": final["outcome"]["evidence_ids"],
        "citation_history_verified": True,
        "exclusion_rows_verified": True,
        "tools": [tool.name for tool in tools],
        "cleanup": final["outcome"].get("cleanup"),
    }


async def main() -> None:
    settings = get_settings()
    report: dict[str, Any] = {
        "phase": "03",
        "timestamp": datetime.now(UTC).isoformat(),
        "model": settings.openai_model,
        "embedding_model": settings.embedding_model,
        "embedding_revision": settings.embedding_revision,
        "embedding_dimensions": settings.embedding_dimension,
        "sandbox_image": settings.sandbox_image,
    }
    path = ROOT / "evals/reports/phase03-2026-10-02.json"

    def save_report() -> None:
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", timeout=90
    ) as client:
        workspace = (
            {"id": os.environ["PHASE03_WORKSPACE_ID"]}
            if os.getenv("PHASE03_WORKSPACE_ID")
            else await request(
                client,
                "POST",
                "/api/workspaces",
                json={"label": "Phase 03 synthetic mixed milestone"},
            )
        )
        report["workspace_id"] = workspace["id"]
        save_report()
        originals = []
        for format in ("pdf", "docx"):
            for language in ("en", "hi"):
                filename = f"applications-{language}.{format}"
                content = (ROOT / "evals/fixtures/v1" / filename).read_bytes()
                response = await request(
                    client,
                    "POST",
                    f"/api/workspaces/{workspace['id']}/documents",
                    files={"file": (filename, content)},
                )
                repeated = await request(
                    client,
                    "POST",
                    f"/api/workspaces/{workspace['id']}/documents",
                    files={"file": (filename, content)},
                )
                assert response["document"]["id"] == repeated["document"]["id"]
                originals.append(
                    (response["source"]["id"], hashlib.sha256(content).hexdigest())
                )
        documents = await ready_documents(client, workspace["id"])
        print("Four digital documents indexed; upload idempotency verified", flush=True)
        report["retrieval"] = await asyncio.to_thread(retrieval_baseline, documents)
        save_report()
        print("Bilingual retrieval measured at ranks 3, 5 and 10", flush=True)
        csv_source = await request(
            client,
            "POST",
            f"/api/workspaces/{workspace['id']}/sources/files",
            files={
                "file": (
                    "applications.csv",
                    (ROOT / "evals/fixtures/v1/applications.csv").read_bytes(),
                )
            },
        )
        connection = await request(
            client,
            "POST",
            f"/api/workspaces/{workspace['id']}/connections",
            json={
                "dialect": "postgresql",
                "host": "127.0.0.1",
                "port": 55432,
                "database_name": "analyst_eval",
                "username": "analyst",
                "password": "analyst",
                "options": {"ssl_mode": "disable"},
            },
        )

        def database_snapshot() -> str:
            with factory()() as session:
                stored_connection = session.scalar(
                    select(Connection).where(
                        Connection.source_id == connection["source"]["id"]
                    )
                )
                assert stored_connection is not None
                result = execute_query(
                    stored_connection,
                    settings,
                    "SELECT * FROM public.synthetic_applications ORDER BY application_id",
                    {"public.synthetic_applications"},
                    100,
                    10,
                )
                return hashlib.sha256(
                    json.dumps(result.rows, sort_keys=True).encode()
                ).hexdigest()

        database_before = await asyncio.to_thread(database_snapshot)
        originals.append(
            (
                csv_source["id"],
                hashlib.sha256(
                    (ROOT / "evals/fixtures/v1/applications.csv").read_bytes()
                ).hexdigest(),
            )
        )
        report["cases"] = []
        for language, filename, source in (
            ("en-IN", "applications-hi.pdf", csv_source),
            ("hi-IN", "applications-en.docx", connection["source"]),
        ):
            document = next(row for row in documents if row["display_name"] == filename)
            report["cases"].append(
                await mixed_case(client, workspace["id"], document, source, language)
            )
            save_report()
            print(
                f"{language}: mixed document + {source['kind']} count 2, INR25000, citations/table/chart verified",
                flush=True,
            )
        database_after = await asyncio.to_thread(database_snapshot)
        assert database_after == database_before
        report["database_rows_unchanged"] = {
            "before": database_before,
            "after": database_after,
        }
        with factory()() as session:
            storage = get_storage(settings)
            for source_id, digest in originals:
                source = session.get(Source, source_id)
                assert source and source.storage_key
                assert (
                    hashlib.sha256(
                        storage.read(source.storage_key, settings.max_upload_bytes)
                    ).hexdigest()
                    == digest
                )
        report["original_hashes_unchanged"] = True
        report["workspace_id"] = workspace["id"]
        thread = await request(
            client,
            "POST",
            f"/api/workspaces/{workspace['id']}/threads",
            json={"label": "Phase 03 unsupported criterion"},
        )
        run = await request(
            client,
            "POST",
            f"/api/threads/{thread['id']}/runs",
            json={
                "text": "What is the annual income threshold for scheme Z9? Search the selected document for Z9. If no supporting criterion exists, ask for the missing scheme document. Do not reuse the S1 threshold.",
                "selected_source_ids": [documents[0]["source_id"]],
                "answer_language": "en-IN",
            },
        )
        unsupported = await wait_terminal(client, run["id"])
        assert unsupported["state"] in {
            "completed",
            "awaiting_clarification",
        }, unsupported
        answer = unsupported["outcome"]["text"].lower()
        assert any(
            word in answer
            for word in (
                "not",
                "no ",
                "cannot",
                "couldn't",
                "missing",
                "please provide",
            )
        ), answer
        report["unsupported_question"] = {
            "run_id": run["id"],
            "state": unsupported["state"],
            "answer": unsupported["outcome"]["text"],
        }
        save_report()
    save_report()
    print(f"Phase03 report saved at {path}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
