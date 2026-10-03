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


async def test_evidence_dependent_hop_and_compressed_citations(db_factory):
    run_id, task = queued_run(db_factory)
    settings = Settings(_env_file=None)
    with db_factory() as session, session.begin():
        run = session.get(Run, run_id)
        thread = session.get(Thread, run.thread_id)
        ids = []
        for name, text in (
            (
                "policy",
                "Scheme S7 eligibility uses the NIRVAAN definition. See the definition handbook.",
            ),
            (
                "handbook",
                "NIRVAAN means net household income. Pension is included; housing aid is excluded.",
            ),
            ("conflicting-old", "NIRVAAN previously excluded pension in scheme S0."),
        ):
            source = Source(
                workspace_id=thread.workspace_id,
                kind="txt",
                state="ready",
                display_name=name,
                content_hash="b" * 64,
            )
            session.add(source)
            session.flush()
            doc = Document(
                source_id=source.id,
                source_version=1,
                extractor_version="test",
                chunker_version="test",
                state="ready",
                stage="indexed",
            )
            session.add(doc)
            session.flush()
            session.add(
                DocumentChunk(
                    document_id=doc.id,
                    ordinal=0,
                    chunker_version="test",
                    text=text,
                    normalized_text=text.lower(),
                    language="en",
                    location={"paragraph": 1},
                    block_ids=[],
                    token_count=30,
                )
            )
            session.flush()
            build_index_generation(session, doc.id, settings)
            ids.append(source.id)
        run.selected_source_ids = ids[:2]
        run.config = {
            **run.config,
            "source_versions": {identity: 1 for identity in ids[:2]},
            "retrieval_profile": "advanced",
        }
    runtime = RunRuntime(task, settings, db_factory)

    async def dispatch(args):
        return await runtime.dispatch(
            "search_documents",
            ModelToolCall(
                id=str(uuid4()),
                name="search_documents",
                arguments=args.model_dump_json(),
            ),
            args,
        )

    first = await dispatch(
        SearchInput(
            query="Scheme S7 eligibility", mode="lexical", limit=1, expand_context=False
        )
    )
    assert first.evidence_ids and "NIRVAAN" in first.data["passages"][0]["excerpt"]
    second = await dispatch(
        SearchInput(
            query="means pension",
            mode="lexical",
            limit=2,
            hop_evidence_ids=first.evidence_ids,
            hop_terms=["NIRVAAN"],
            compress=True,
            expand_context=False,
        )
    )
    assert any("net household income" in p["excerpt"] for p in second.data["passages"])
    assert any(t["stage"] == "dependent_hop" for t in second.data["trace"])
    assert all(p["source_id"] != ids[2] for p in second.data["passages"])
    with db_factory() as session:
        for identity in second.evidence_ids:
            evidence = get_evidence(str(identity), session)
            assert evidence["excerpt"] and evidence["document_version"] == 1
        validate_answer(
            session,
            session.get(Run, run_id),
            FinalAnswer(text="Pension included", evidence_ids=second.evidence_ids),
        )
    independent = await dispatch(
        SearchInput(
            query="income definitions",
            mode="lexical",
            subquestions=["Scheme S7", "NIRVAAN pension"],
            expand_context=False,
        )
    )
    assert any(t.get("kind") == "independent" for t in independent.data["trace"])
    assert len({p["source_id"] for p in independent.data["passages"]}) == 2
    rejected = await dispatch(
        SearchInput(
            query="means",
            mode="lexical",
            hop_evidence_ids=first.evidence_ids,
            hop_terms=["invented"],
        )
    )
    assert rejected.status == "failed" and rejected.error.code == "invalid_hop_terms"
    with db_factory() as session, session.begin():
        row = session.get(Evidence, str(first.evidence_ids[0]))
        row.details = {**row.details, "source_versions": {ids[0]: 2}}
    rejected = await dispatch(
        SearchInput(
            query="means",
            mode="lexical",
            hop_evidence_ids=first.evidence_ids,
            hop_terms=["NIRVAAN"],
        )
    )
    assert rejected.status == "failed" and rejected.error.code == "invalid_hop_evidence"


async def test_historical_artifact_inspection_without_sandbox(
    db_factory, tmp_path, monkeypatch
):
    from app.agent.runtime import InspectInput
    from app.storage.filesystem import FileStorage
    from app.db.models import Artifact
    import app.agent.runtime as module

    run_id, task = queued_run(db_factory)
    storage = FileStorage(tmp_path)
    monkeypatch.setattr(module, "get_storage", lambda settings: storage)
    with db_factory() as session, session.begin():
        run = session.get(Run, run_id)
        earlier = Run(
            thread_id=run.thread_id,
            state="completed",
            config={"sandbox_session_id": "expired", "source_versions": {}},
            selected_source_ids=[],
        )
        session.add(earlier)
        session.flush()
        stored = storage.put(
            f"derived/{earlier.id}/retained.csv", b"unit,total\nINR,25000.00\n"
        )
        artifact = Artifact(
            run_id=earlier.id,
            storage_key=stored.key,
            display_name="retained.csv",
            media_type="text/csv",
            sha256=stored.sha256,
            byte_size=stored.byte_size,
        )
        session.add(artifact)
        session.flush()
        identity = artifact.id
    runtime = RunRuntime(
        task, Settings(_env_file=None, storage_root=tmp_path), db_factory
    )
    args = InspectInput(artifact_id=identity)
    result = await runtime.dispatch(
        "inspect_artifact",
        ModelToolCall(
            id=str(uuid4()), name="inspect_artifact", arguments=args.model_dump_json()
        ),
        args,
    )
    assert result.status == "ok" and "25000.00" in result.summary
    assert runtime.sandbox is None
    with db_factory() as session:
        validate_answer(
            session,
            session.get(Run, run_id),
            FinalAnswer(text="Retained result", artifact_ids=[identity]),
        )
