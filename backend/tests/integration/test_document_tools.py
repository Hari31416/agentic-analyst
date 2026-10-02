"""Real lexical retrieval through agent dispatch, selected scope, and saved citations."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.agent.protocol import ModelToolCall
from app.agent.runtime import RunRuntime
from app.api.evidence import get_evidence
from app.config import Settings
from app.contracts import FinalAnswer
from app.db.models import Document, DocumentChunk, Evidence, Run, Source, Thread
from app.evidence.validation import validate_answer
from app.retrieval.service import build_index_generation
from app.tools.documents import PassageInput, SearchInput
from tests.integration.test_runtime import db_factory, queued_run

pytestmark = pytest.mark.integration


async def test_document_tools_pin_selected_versions_and_reopen_citations(db_factory):
    run_id, task = queued_run(db_factory)
    settings = Settings(_env_file=None)
    with db_factory() as session, session.begin():
        run = session.get(Run, run_id)
        thread = session.get(Thread, run.thread_id)
        chunks = []
        for label in ("selected", "excluded"):
            source = Source(
                workspace_id=thread.workspace_id,
                kind="pdf",
                state="ready",
                display_name=f"{label}.pdf",
                content_hash="a" * 64,
            )
            session.add(source)
            session.flush()
            document = Document(
                source_id=source.id,
                source_version=1,
                extractor_version="pdf-test",
                chunker_version="test",
                state="ready",
                stage="indexed",
            )
            session.add(document)
            session.flush()
            chunk = DocumentChunk(
                document_id=document.id,
                ordinal=0,
                chunker_version="test",
                text="Qualifies when scheme S1 is active and income is at most INR 200000 inclusive.",
                normalized_text="qualifies when scheme s1 is active and income is at most inr 200000 inclusive.",
                language="en",
                location={"page": 1},
                block_ids=[],
                token_count=20,
            )
            session.add(chunk)
            session.flush()
            build_index_generation(session, document.id, settings)
            chunks.append(chunk.id)
            if label == "selected":
                selected_id, document_id = source.id, document.id
        run.selected_source_ids = [selected_id]
        run.config = {**run.config, "source_versions": {selected_id: 1}}
    runtime = RunRuntime(task, settings, db_factory)
    args = SearchInput(query="income inclusive", mode="lexical")
    found = await runtime.dispatch(
        "search_documents",
        ModelToolCall(
            id=str(uuid4()), name="search_documents", arguments=args.model_dump_json()
        ),
        args,
    )
    assert found.status == "ok" and len(found.evidence_ids) == 1
    assert found.data["mode"] == "lexical"
    denied_args = PassageInput(chunk_id=UUID(chunks[1]))
    denied = await runtime.dispatch(
        "source_passage",
        ModelToolCall(
            id=str(uuid4()),
            name="source_passage",
            arguments=denied_args.model_dump_json(),
        ),
        denied_args,
    )
    assert denied.status == "failed" and not denied.evidence_ids
    with db_factory() as session:
        saved = get_evidence(str(found.evidence_ids[0]), session)
        assert saved["document_id"] == document_id
        assert saved["location"] == {"page": 1} and saved["document_version"] == 1
        run = session.get(Run, run_id)
        answer = FinalAnswer(
            text="The limit is inclusive", evidence_ids=found.evidence_ids
        )
        validate_answer(session, run, answer)
        run.config = {**run.config, "source_versions": {selected_id: 2}}
        with pytest.raises(ValueError, match="source version"):
            validate_answer(session, run, answer)
        # Citation history remains readable at its original version despite a later selection.
        assert (
            get_evidence(str(found.evidence_ids[0]), session)["excerpt"]
            == saved["excerpt"]
        )
