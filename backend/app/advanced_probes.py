"""Explicit phase 05 synthetic stage comparisons and bilingual live trials."""

import argparse
import asyncio
import hashlib
import json
import resource
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Document, Source, ToolCall
from app.db.session import factory
from app.document_probes import supports_criterion
from app.probes import request, wait_terminal
from app.retrieval.advanced import advanced_search
from app.retrieval.summaries import summarize

ROOT = Path(__file__).resolve().parents[2]


def ablations() -> dict[str, Any]:
    settings = get_settings()
    saved = json.loads((ROOT / "evals/reports/phase03-2026-10-02.json").read_text())
    with factory()() as session:
        docs = list(
            session.scalars(
                select(Document)
                .join(Source, Source.id == Document.source_id)
                .where(
                    Source.workspace_id == saved["workspace_id"],
                    Document.state == "ready",
                    Source.state != "deleted",
                )
            )
        )
        assert len(docs) == 4, "Saved synthetic corpus unavailable"
        versions = {doc.source_id: doc.source_version for doc in docs}
        rows = []
        for language, query in [
            (
                "en",
                "scheme S1 active annual income maximum eligibility inclusive threshold",
            ),
            ("hi", "योजना S1 सक्रिय वार्षिक आय अधिकतम पात्रता समावेशी सीमा"),
        ]:
            stages: list[tuple[str, dict[str, Any]]] = [
                ("basic", {"profile": "basic"}),
                ("multi_query", {"expand": False, "diversity": False}),
                (
                    "rerank",
                    {
                        "multi_query": False,
                        "expand": False,
                        "rerank": True,
                        "diversity": False,
                    },
                ),
                (
                    "expansion",
                    {"multi_query": False, "expand": True, "diversity": False},
                ),
                (
                    "compression",
                    {
                        "multi_query": False,
                        "expand": False,
                        "compress": True,
                        "diversity": False,
                    },
                ),
                ("consensus", {"expand": False, "diversity": False, "consensus": True}),
                ("coverage", {"multi_query": False, "expand": False}),
                ("combined", {"rerank": True, "compress": True}),
            ]
            for stage, options in stages:
                for trial in range(2):
                    started = time.monotonic()
                    result = advanced_search(
                        session,
                        query,
                        versions,
                        settings,
                        limit=10,
                        context_budget=12000,
                        token_budget=24000,
                        **options,
                    )
                    ranks = {
                        doc.id: min(
                            (
                                i
                                for i, p in enumerate(result["passages"], 1)
                                if p["document_id"] == doc.id and supports_criterion(p)
                            ),
                            default=None,
                        )
                        for doc in docs
                    }
                    rows.append(
                        {
                            "language": language,
                            "stage": stage,
                            "trial": trial + 1,
                            "latency_ms": round((time.monotonic() - started) * 1000, 2),
                            "ranks": ranks,
                            "recall_at": {
                                str(k): sum(
                                    r is not None and r <= k for r in ranks.values()
                                )
                                / len(docs)
                                for k in (3, 5, 10)
                            },
                            "excerpt_characters": sum(
                                len(p["excerpt"]) for p in result["passages"]
                            ),
                            "degraded": result["degraded"],
                            "model_calls": 0,
                            "trace": result["trace"],
                        }
                    )
        started = time.monotonic()
        first = summarize(session, versions, thematic=True)
        session.commit()
        second = summarize(session, versions, thematic=True)
        assert second["cache"]["hit"]
        return {
            "workspace_id": saved["workspace_id"],
            "cases": rows,
            "summary": {
                "cache_hit": True,
                "fingerprint": first["cache"]["fingerprint"],
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "method": first["method"],
                "support_count": len(first["supporting_passages"]),
            },
            "peak_process_memory_bytes": resource.getrusage(
                resource.RUSAGE_SELF
            ).ru_maxrss,
        }


async def live() -> None:
    settings = get_settings()
    report: dict[str, Any] = {
        "phase": "05",
        "timestamp": datetime.now(UTC).isoformat(),
        "model": settings.openai_model,
        "prompt_version": "analyst-v3",
        "pipeline": "advanced-retrieval-v1",
        "embedding_model": settings.embedding_model,
        "embedding_revision": settings.embedding_revision,
        "reranker_model": settings.reranker_model,
        "reranker_revision": settings.reranker_revision,
        "retrieval_ablation": await asyncio.to_thread(ablations),
        "live_trials": [],
    }
    path = ROOT / "evals/reports/phase05-2026-10-03.json"

    def save() -> None:
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
        )

    save()
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", timeout=120
    ) as client:
        workspace = await request(
            client,
            "POST",
            "/api/workspaces",
            json={"label": "Phase05 synthetic hop evaluation"},
        )
        source_ids = []
        original_hashes = {}
        for name, content in [
            (
                "scheme.md",
                "# Scheme S7\n\nScheme S7 eligibility uses the NIRVAAN definition for net household income. The definition is in the bilingual handbook. Do not use superseded Scheme S0 rules.\n",
            ),
            (
                "handbook-en.md",
                "# NIRVAAN definition\n\nNIRVAAN means net household income. Pension is included. Housing aid is excluded. Scheme S7 has no dependency on Scheme S0.\n",
            ),
            (
                "handbook-hi.md",
                "# NIRVAAN परिभाषा\n\nNIRVAAN का अर्थ शुद्ध घरेलू आय है। पेंशन शामिल है। आवास सहायता शामिल नहीं है। यह नियम योजना S7 के लिए है।\n",
            ),
            (
                "superseded.md",
                "# Superseded S0\n\nScheme S0 excludes pension. This version is obsolete and is not the Scheme S7 definition.\n",
            ),
        ]:
            result = await request(
                client,
                "POST",
                f"/api/workspaces/{workspace['id']}/documents",
                files={"file": (name, content.encode(), "text/markdown")},
            )
            sid = result["source"]["id"] if "source" in result else result["source_id"]
            source_ids.append(sid)
            original_hashes[sid] = hashlib.sha256(content.encode()).hexdigest()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            docs = await request(
                client, "GET", f"/api/workspaces/{workspace['id']}/documents"
            )
            if all(d["state"] == "ready" for d in docs):
                break
            if any(d["state"] == "failed" for d in docs):
                raise AssertionError("Synthetic ingestion failed")
            await asyncio.sleep(1)
        else:
            raise AssertionError("Synthetic ingestion timeout")
        for language in ("en-IN", "hi-IN"):
            for trial in range(2):
                thread = await request(
                    client,
                    "POST",
                    f"/api/workspaces/{workspace['id']}/threads",
                    json={"label": f"{language} trial {trial+1}"},
                )
                prompt = "Find how Scheme S7 treats pension and housing aid. First search_documents for Scheme S7 eligibility, lexical mode, limit1 and expand_context=false. Read the definition identifier in the evidence. Then call search_documents again with hop_evidence_ids from that first search and hop_terms copied verbatim from that excerpt to locate the definition in the handbook. In this second search use rerank=true and compress=true. Also call summarize_documents scope=overview thematic=true. Finish with pension included and housing aid excluded, citing original supporting evidence. Do not infer rules from a summary alone."
                started = time.monotonic()
                run = await request(
                    client,
                    "POST",
                    f"/api/threads/{thread['id']}/runs",
                    json={
                        "text": prompt,
                        "answer_language": language,
                        "selected_source_ids": source_ids[:3],
                        "retrieval_profile": "advanced",
                    },
                )
                result = await wait_terminal(client, run["id"])
                with factory()() as session:
                    calls = list(
                        session.scalars(
                            select(ToolCall)
                            .where(ToolCall.run_id == run["id"])
                            .order_by(ToolCall.created_at)
                        )
                    )
                    tools = [
                        {
                            "name": c.name,
                            "status": c.status,
                            "result_status": (c.result or {}).get("status"),
                            "trace": (c.result or {}).get("data", {}).get("trace", []),
                        }
                        for c in calls
                    ]
                dependent = any(
                    isinstance(c["trace"], list)
                    and any(t.get("stage") == "dependent_hop" for t in c["trace"])
                    for c in tools
                )
                summary_ran = any(
                    c["name"] == "summarize_documents"
                    and c["result_status"] in {"ok", "partial"}
                    for c in tools
                )
                report["live_trials"].append(
                    {
                        "language": language,
                        "trial": trial + 1,
                        "run_id": run["id"],
                        "thread_id": thread["id"],
                        "state": result["state"],
                        "latency_ms": round((time.monotonic() - started) * 1000, 2),
                        "dependent_hop": dependent,
                        "summary_ran": summary_ran,
                        "outcome": result["outcome"],
                        "tools": tools,
                    }
                )
                save()
                if result["state"] == "completed":
                    for identity in result["outcome"].get("evidence_ids", []):
                        evidence = await request(
                            client, "GET", f"/api/evidence/{identity}"
                        )
                        assert evidence["excerpt"] and set(
                            evidence["source_ids"]
                        ).issubset(set(source_ids[:3]))
        with factory()() as session:
            for sid, digest in original_hashes.items():
                source = session.get(Source, sid)
                assert source is not None and source.content_hash == digest
        report["source_integrity"] = True
        save()
    print(
        json.dumps(
            {
                "report": str(path),
                "trials": [
                    {
                        k: row[k]
                        for k in (
                            "language",
                            "trial",
                            "state",
                            "dependent_hop",
                            "summary_ran",
                        )
                    }
                    for row in report["live_trials"]
                ],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--retrieval-only", action="store_true")
    args = parser.parse_args()
    if args.retrieval_only:
        path = ROOT / "evals/reports/phase05-2026-10-03.json"
        report = json.loads(path.read_text()) if path.exists() else {"phase": "05"}
        report["retrieval_ablation"] = ablations()
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
        )
        print("Updated local retrieval comparisons; retained existing live trials.")
    else:
        asyncio.run(live())
