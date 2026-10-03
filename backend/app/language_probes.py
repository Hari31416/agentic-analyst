"""Local phase-seven baseline measurements, using retained synthetic documents/audio."""

import asyncio
import hashlib
import json
import resource
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Document, Source
from app.db.session import factory
from app.document_probes import supports_criterion
from app.language.speech import capabilities, transcribe_audio
from app.retrieval.advanced import advanced_search

ROOT = Path(__file__).resolve().parents[2]


def retrieval_cases() -> list[dict[str, Any]]:
    settings = get_settings()
    saved = json.loads((ROOT / "evals/reports/phase03-2026-10-02.json").read_text())
    cases = []
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
        assert len(docs) == 4
        versions = {doc.source_id: doc.source_version for doc in docs}
        for language, query in [
            (
                "en",
                "scheme S1 active annual income maximum eligibility inclusive threshold",
            ),
            ("hi", "योजना S1 सक्रिय वार्षिक आय अधिकतम पात्रता समावेशी सीमा"),
            (
                "hi-Latn",
                "yojana S1 sakriya varshik aay adhiktam patrata samaveshi seema",
            ),
            ("ambiguous", "kal Ram APP-001"),
        ]:
            for profile in ["basic", "advanced"]:
                start = time.monotonic()
                result = advanced_search(
                    session,
                    query,
                    versions,
                    settings,
                    profile=profile,
                    mode="hybrid",
                    limit=10,
                    context_budget=20000,
                    token_budget=60000,
                    expand=False,
                    rerank=False,
                    diversity=False,
                )
                passages = result["passages"]
                ranks: dict[str, int] = {}
                for rank, item in enumerate(passages, 1):
                    if supports_criterion(item):
                        ranks.setdefault(item["document_id"], rank)
                cases.append(
                    {
                        "language": language,
                        "query": query,
                        "profile": profile,
                        "latency_ms": round((time.monotonic() - start) * 1000, 2),
                        "criterion_document_ranks": ranks,
                        "recall_at": {
                            str(k): sum(r <= k for r in ranks.values()) / len(docs)
                            for k in [3, 5, 10]
                        },
                        "non_supporting_passages_at_3": sum(
                            not supports_criterion(p) for p in passages[:3]
                        ),
                        "trace": result["trace"],
                        "unresolved_ambiguity": language == "ambiguous",
                        "measurement_note": "Advanced includes bounded keyword/fusion stages as well as glossary variants; this is not a glossary-only ablation. Ambiguous case has no asserted eligibility intent.",
                    }
                )
    return cases


async def main() -> None:
    report: dict[str, Any] = {
        "phase": "07",
        "timestamp": datetime.now(UTC).isoformat(),
        "capabilities": capabilities(),
        "retrieval": retrieval_cases(),
        "speech": [],
    }
    for language in ["en", "hi"]:
        audio = ROOT / f"evals/fixtures/voice-v1/{language}.wav"
        start = time.monotonic()
        case: dict[str, Any] = {
            "language": language,
            "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
        }
        try:
            case["result"] = await transcribe_audio(
                audio.read_bytes(), language + "-IN"
            )
            case["status"] = (
                "transcribed" if case["result"]["text"].strip() else "empty_transcript"
            )
        except Exception as exc:
            case.update(
                status="failed", error=type(exc).__name__, message=str(exc)[:200]
            )
        case["latency_seconds"] = round(time.monotonic() - start, 3)
        report["speech"].append(case)
    report["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report["limits"] = (
        "Language/ASR quality is descriptive. Original text/audio fixtures are unchanged; no retries, translation provider or TTS inference."
    )
    path = ROOT / f"evals/reports/phase07-{datetime.now(UTC):%Y%m%dT%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Language baseline measurements retained at {path.relative_to(ROOT)}")
    for c in report["speech"]:
        print(
            c["language"],
            c["status"],
            c.get("result", {}).get("text", c.get("message")),
        )


if __name__ == "__main__":
    asyncio.run(main())
