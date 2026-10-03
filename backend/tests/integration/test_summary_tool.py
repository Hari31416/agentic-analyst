"""Summary tool keeps cached prose tied to selected source evidence."""

from uuid import uuid4

import pytest

from app.agent.runtime import RunRuntime
from app.config import Settings
from app.db.models import Document, DocumentChunk, Run, Source, Thread
from app.tools.summaries import SummaryInput, SummaryTools
from tests.integration.test_runtime import db_factory, queued_run

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_document_summary_cache_and_source_version_evidence(db_factory):
    run_id, task = queued_run(db_factory)
    with db_factory() as session, session.begin():
        run = session.get(Run, run_id)
        thread = session.get(Thread, run.thread_id)
        source = Source(
            workspace_id=thread.workspace_id,
            kind="pdf",
            state="ready",
            display_name="benefits.pdf",
            content_hash="b" * 64,
        )
        session.add(source)
        session.flush()
        document = Document(
            source_id=source.id,
            source_version=1,
            extractor_version="extract-v1",
            chunker_version="chunk-v1",
            state="ready",
            stage="indexed",
        )
        session.add(document)
        session.flush()
        chunks = [
            DocumentChunk(
                document_id=document.id,
                ordinal=100 + ordinal,
                chunker_version="chunk-v1",
                text=text,
                normalized_text=text.casefold(),
                heading=heading,
                location={"page": ordinal + 1},
                language="en",
                block_ids=[],
                token_count=14,
            )
            for ordinal, (heading, text) in enumerate(
                [
                    (
                        "Eligibility",
                        "Applicants must live in the district. Household income must not exceed INR 200000.",
                    ),
                    (
                        "Payment",
                        "Approved households receive a monthly transfer of INR 5000.",
                    ),
                ]
            )
        ]
        session.add_all(chunks)
        session.flush()
        run.selected_source_ids = [source.id]
        run.config = {
            **run.config,
            "source_versions": {source.id: source.version},
        }
        source_id = source.id

    runtime = RunRuntime(task, Settings(_env_file=None), db_factory)
    tool = SummaryTools(runtime)
    first = await tool.execute(
        "summarize_documents",
        SummaryInput(scope="document", source_id=source_id),
        str(uuid4()),
    )
    second = await tool.execute(
        "summarize_documents",
        SummaryInput(scope="document", source_id=source_id),
        str(uuid4()),
    )
    assert first.status == "ok" and first.evidence_ids
    assert first.data["cache"]["hit"] is False
    assert second.data["cache"]["hit"] is True
    assert first.data["method"]["model_calls"] == 0
    assert "INR 200000" in first.data["summary"]
    assert all(row["evidence_id"] for row in second.data["supporting_passages"])

    section = await tool.execute(
        "summarize_documents",
        SummaryInput(scope="section", source_id=source_id, section="Eligibility"),
        str(uuid4()),
    )
    assert section.status == "ok"
    assert section.data["documents"][0]["sampled_chunks"] == 1
    assert "200000" in section.data["summary"]

    with db_factory() as session, session.begin():
        document = session.query(Document).filter_by(source_id=source_id).one()
        document.extractor_version = "extract-v2"

    invalidated = await tool.execute(
        "summarize_documents",
        SummaryInput(scope="document", source_id=source_id),
        str(uuid4()),
    )
    assert invalidated.data["cache"]["hit"] is False
    assert (
        invalidated.data["cache"]["fingerprint"] != first.data["cache"]["fingerprint"]
    )
