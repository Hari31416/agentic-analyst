"""Recall smoke test over the checked-in English/Hindi PDF and DOCX fixtures."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Base, Document, DocumentChunk, Source, Workspace
from app.retrieval.service import search
from app.sources.documents import CHUNKER_VERSION, EXTRACTOR_VERSION, process_document

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("LIVE_EMBEDDING_TESTS") != "1",
        reason="set LIVE_EMBEDDING_TESTS=1 to load pinned local model assets",
    ),
]

_REPO = Path(__file__).resolve().parents[3]
_FIXTURE_DIR = _REPO / "evals" / "fixtures" / "v1"
_MODEL_DIR = _REPO / "data" / "models" / "multilingual-e5-small"


class _FixtureStorage:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = blobs

    def read(self, key: str, max_bytes: int) -> bytes:
        value = self._blobs[key]
        if len(value) > max_bytes:
            raise ValueError("fixture exceeds byte limit")
        return value


@pytest.fixture
def dbfactory() -> Iterator[sessionmaker[Session]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = "bilingual_rag_" + uuid4().hex
    admin = create_engine(database_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        database_url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    # Force creation in the new schema; public is visible only for pgvector.
    Base.metadata.create_all(engine, checkfirst=False)
    try:
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_four_document_bilingual_recall_at_20(
    dbfactory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.storage import factory as storage_factory

    settings = Settings(
        embedding_model="intfloat/multilingual-e5-small",
        embedding_model_path=_MODEL_DIR,
        embedding_dimension=384,
        embedding_revision="761b726dd34fb83930e26aab4e9ac3899aa1fa78",
    )
    assets = [
        _FIXTURE_DIR / "applications-en.pdf",
        _FIXTURE_DIR / "applications-en.docx",
        _FIXTURE_DIR / "applications-hi.pdf",
        _FIXTURE_DIR / "applications-hi.docx",
    ]
    blobs: dict[str, bytes] = {}
    docs: dict[str, tuple[str, str]] = {}
    with dbfactory() as session, session.begin():
        workspace = Workspace(label="bilingual retrieval fixture")
        session.add(workspace)
        session.flush()
        for path in assets:
            if not path.is_file():
                pytest.fail(f"Expected fixture missing: {path.name}")
            language = "hi" if "-hi." in path.name else "en"
            data = path.read_bytes()
            storage_key = f"fixture/{path.name}"
            source = Source(
                workspace_id=workspace.id,
                kind=path.suffix.removeprefix("."),
                version=1,
                display_name=path.name,
                state="uploaded",
                storage_key=storage_key,
                content_hash=hashlib.sha256(data).hexdigest(),
                details={},
            )
            session.add(source)
            session.flush()
            document = Document(
                source_id=source.id,
                source_version=1,
                extractor_version=EXTRACTOR_VERSION,
                chunker_version=CHUNKER_VERSION,
                state="queued",
                stage="queued",
                progress=0,
                details={},
            )
            session.add(document)
            session.flush()
            blobs[storage_key] = data
            docs[language + path.suffix] = (source.id, document.id)

    monkeypatch.setattr(
        storage_factory, "get_storage", lambda _settings: _FixtureStorage(blobs)
    )
    for _key, (_source_id, document_id) in docs.items():
        result = process_document(document_id, settings, dbfactory)
        assert result["state"] == "ready"
        assert result["stage"] == "indexed"

    selected_sources: dict[str, int] = {source_id: 1 for source_id, _ in docs.values()}
    expected_ids = {document_id for _source_id, document_id in docs.values()}
    document_language = {
        document_id: language
        for key, (_source_id, document_id) in docs.items()
        for language in ("hi" if key.startswith("hi") else "en",)
    }
    document_labels = {
        document_id: label for label, (_source_id, document_id) in docs.items()
    }
    support_ids: dict[str, set[str]] = {
        document_id: set() for document_id in expected_ids
    }
    with dbfactory() as session:
        chunks = list(
            session.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id.in_(expected_ids))
            )
        )
        for chunk in chunks:
            document_lang = document_language.get(chunk.document_id)
            lowered = chunk.text.casefold()
            if (
                document_lang == "en"
                and "qualifies" in lowered
                and "s1" in lowered
                and "200,000" in lowered
            ):
                support_ids[chunk.document_id].add(chunk.id)
            elif (
                document_lang == "hi"
                and "पात्र" in lowered
                and "s1" in lowered
                and "200000" in lowered
            ):
                support_ids[chunk.document_id].add(chunk.id)
        assert all(support_ids.values()), "Each PDF/DOCX needs a labeled rule chunk"
        queries = {
            "en": "scheme S1 active annual income maximum eligibility inclusive threshold",
            "hi": "योजना S1 सक्रिय वार्षिक आय अधिकतम पात्रता समावेशी सीमा",
        }
        results: dict[tuple[str, str], dict[str, object]] = {
            (language, mode): search(
                session,
                query,
                selected_sources,
                settings,
                mode=mode,
                limit=20,
                context_budget=20_000,
            )
            for language, query in queries.items()
            for mode in ("text", "vector", "hybrid")
        }
        short_lexical = {
            "en": search(
                session,
                "income",
                selected_sources,
                settings,
                mode="text",
                limit=20,
            ),
            "hi": search(
                session,
                "आय",
                selected_sources,
                settings,
                mode="text",
                limit=20,
            ),
        }

    def labeled_ranks(result: dict[str, object]) -> dict[str, int | None]:
        passages = result["passages"]
        assert isinstance(passages, list)
        ranks: dict[str, int | None] = {}
        for document_id, chunk_ids in support_ids.items():
            matching = [
                int(item["rank"])
                for item in passages
                if item["document_id"] == document_id and item["chunk_id"] in chunk_ids
            ]
            ranks[document_labels[document_id]] = min(matching) if matching else None
        return ranks

    document_recalls: dict[str, float] = {}
    support_metrics: dict[str, dict[str, object]] = {}
    for (language, mode), result in results.items():
        ranks = labeled_ranks(result)
        relevant = list(ranks.values())
        recalls: dict[str, float] = {}
        for cutoff in (3, 5, 10):
            recall = sum(
                rank is not None and rank <= cutoff for rank in relevant
            ) / len(relevant)
            recalls[str(cutoff)] = recall
        passages = result["passages"]
        assert isinstance(passages, list)
        found_ids = {str(item["document_id"]) for item in passages}
        document_recalls[f"{language}/{mode}"] = len(expected_ids & found_ids) / len(
            expected_ids
        )
        assert result["degraded"] is False
        support_metrics[f"{language}/{mode}"] = {
            "ranks": ranks,
            "recall_at": recalls,
            "document_recall_at_20": document_recalls[f"{language}/{mode}"],
            "trace": result["trace"],
        }
    lexical_diagnostics: dict[str, dict[str, object]] = {}
    for language, result in short_lexical.items():
        passages = result["passages"]
        assert isinstance(passages, list)
        found_labels = sorted(
            {
                document_labels[str(item["document_id"])]
                for item in passages
                if str(item["document_id"]) in document_labels
            }
        )
        lexical_diagnostics[language] = {
            "query": "income" if language == "en" else "आय",
            "candidate_count": len(passages),
            "documents": found_labels,
            "trace": result["trace"],
        }
    print(
        "V2 document recall@20 by query/mode: "
        f"{document_recalls}; support metrics={support_metrics}; "
        f"short lexical diagnostics={lexical_diagnostics}; "
        "previous chunker-v1 hybrid support baseline was 2/8 hits at @3."
    )
    assert all(
        document_recalls[f"{language}/{mode}"] == 1.0
        for language in ("en", "hi")
        for mode in ("vector", "hybrid")
    )
    report_path = os.getenv("RETRIEVAL_EVAL_OUTPUT")
    if report_path:
        Path(report_path).write_text(
            json.dumps(
                {
                    "chunker_version": CHUNKER_VERSION,
                    "extractor_version": EXTRACTOR_VERSION,
                    "documents": sorted(document_labels.values()),
                    "queries": queries,
                    "support_metrics": support_metrics,
                    "short_lexical_diagnostics": lexical_diagnostics,
                    "previous_chunker_v1_hybrid_support_hits_at_3": "2/8",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
