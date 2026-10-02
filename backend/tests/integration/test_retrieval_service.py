"""Database-backed retrieval checks; set TEST_DATABASE_URL to PostgreSQL."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

import app.retrieval.service as retrieval
from app.config import Settings
from app.db.models import (
    Base,
    ChunkEmbedding,
    Document,
    DocumentChunk,
    IndexGeneration,
    Source,
    Workspace,
)
from app.retrieval.embedding import EmbeddingUnavailable
from app.retrieval.service import (
    RetrievalError,
    build_index_generation,
    get_passage,
    search,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = "retrieval_" + uuid4().hex
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


class FakeAdapter:
    model_id = "intfloat/multilingual-e5-small"
    revision = "test-revision"
    dimensions = 384

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * 384
        vector[0 if "eligibility" in text.lower() else 1] = 1.0
        return vector


def _settings() -> Settings:
    return Settings(
        embedding_model=FakeAdapter.model_id,
        embedding_revision=FakeAdapter.revision,
        embedding_dimension=FakeAdapter.dimensions,
    )


def _create_document(session: Session, *, name: str = "rules.pdf") -> Document:
    workspace = Workspace(label="retrieval test")
    session.add(workspace)
    session.flush()
    source = Source(
        workspace_id=workspace.id,
        kind="pdf",
        version=1,
        display_name=name,
        state="ready",
        content_hash="a" * 64,
        details={},
    )
    session.add(source)
    session.flush()
    document = Document(
        source_id=source.id,
        source_version=1,
        extractor_version="extract-v1",
        chunker_version="chunk-v1",
        state="ready",
        stage="chunked",
        progress=90,
        details={},
    )
    session.add(document)
    session.flush()
    session.add_all(
        [
            DocumentChunk(
                document_id=document.id,
                ordinal=0,
                chunker_version="chunk-v1",
                text="Eligibility requires an active scheme and annual income no more than the threshold.",
                normalized_text="eligibility requires an active scheme and annual income no more than the threshold",
                heading="Eligibility rule",
                location={"page_start": 1, "page_end": 1, "char_start": 10},
                language="en-IN",
                block_ids=[],
                token_count=14,
            ),
            DocumentChunk(
                document_id=document.id,
                ordinal=1,
                chunker_version="chunk-v1",
                text="Contact the local office for application status and further information.",
                normalized_text="contact the local office for application status and further information",
                heading="Next steps",
                location={"page_start": 2, "page_end": 2, "char_start": 300},
                language="en-IN",
                block_ids=[],
                token_count=11,
            ),
        ]
    )
    session.flush()
    return document


def test_generation_search_scope_and_passage_expansion(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        retrieval, "get_embedding_adapter", lambda _settings: FakeAdapter()
    )
    settings = _settings()
    with session_factory() as session, session.begin():
        document = _create_document(session)
        source_id = document.source_id
        generation = build_index_generation(session, document.id, settings)
        assert generation.status == "ready"
        assert generation.dimensions == 384
        assert generation.config_json["model_revision"] == FakeAdapter.revision
        assert document.index_generation_id == generation.id

        result = search(
            session,
            "eligibility annual income threshold",
            {source_id: 1},
            settings,
            mode="hybrid",
            limit=2,
        )
        assert result["mode"] == "hybrid"
        assert result["degraded"] is False
        assert result["passages"][0]["heading"] == "Eligibility rule"
        assert result["passages"][0]["lexical_rank"] == 1
        assert result["passages"][0]["dense_rank"] == 1
        assert result["passages"][0]["generation_id"] == generation.id

        out_of_scope = search(
            session,
            "eligibility annual income threshold",
            {source_id: 2},
            settings,
            mode="text",
        )
        assert out_of_scope["passages"] == []
        with pytest.raises(RetrievalError) as forbidden:
            get_passage(
                session, result["passages"][0]["chunk_id"], {source_id: 2}, settings
            )
        assert forbidden.value.code == "passage_not_found"

        passage = get_passage(
            session,
            result["passages"][0]["chunk_id"],
            {source_id: 1},
            settings,
            neighbors=1,
            context_budget=500,
        )
        assert passage["location"]["page_start"] == 1
        assert len(passage["neighbors"]) == 2
        assert "Contact the local office" in passage["context"]


def test_degraded_generation_keeps_lexical_retrieval_and_dense_is_unavailable(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(_settings: Settings) -> FakeAdapter:
        raise EmbeddingUnavailable("embedding_assets_missing", "assets unavailable")

    monkeypatch.setattr(retrieval, "get_embedding_adapter", unavailable)
    settings = _settings()
    with session_factory() as session, session.begin():
        document = _create_document(session)
        generation = build_index_generation(session, document.id, settings)
        assert generation.status == "degraded"
        assert generation.config_json["error_code"] == "embedding_assets_missing"
        result = search(
            session,
            "eligibility annual income",
            {document.source_id: 1},
            settings,
            mode="hybrid",
        )
        assert result["requested_mode"] == "hybrid"
        assert result["mode"] == "text"
        assert result["degraded"] is True
        assert result["passages"][0]["chunk_id"]
        with pytest.raises(RetrievalError) as unavailable_dense:
            search(
                session,
                "eligibility annual income",
                {document.source_id: 1},
                settings,
                mode="vector",
            )
        assert unavailable_dense.value.code == "embedding_assets_missing"


def test_existing_degraded_generation_is_retried_when_assets_return(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    unavailable = lambda _settings: (_ for _ in ()).throw(
        EmbeddingUnavailable("embedding_assets_missing", "assets unavailable")
    )
    monkeypatch.setattr(retrieval, "get_embedding_adapter", unavailable)
    with session_factory() as session, session.begin():
        document = _create_document(session)
        first = build_index_generation(session, document.id, _settings())
        assert first.status == "degraded"
        fingerprint = first.fingerprint

    monkeypatch.setattr(
        retrieval, "get_embedding_adapter", lambda _settings: FakeAdapter()
    )
    with session_factory() as session, session.begin():
        retrieved_document = session.get(Document, document.id)
        assert retrieved_document is not None
        generation = build_index_generation(session, retrieved_document.id, _settings())
        assert generation.status == "ready"
        assert generation.id == first.id
        assert (
            generation.fingerprint != fingerprint
            or "fastembed_version" in generation.config_json
        )


def test_lease_guard_runs_after_inference_before_generation_publication(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    embedded = False

    class TrackingAdapter(FakeAdapter):
        def embed_passages(self, texts: list[str]) -> list[list[float]]:
            nonlocal embedded
            embedded = True
            return super().embed_passages(texts)

    monkeypatch.setattr(
        retrieval, "get_embedding_adapter", lambda _settings: TrackingAdapter()
    )
    with session_factory() as session:
        document = _create_document(session)
        document_id = document.id
        session.commit()

        def reject_lease(_session: Session) -> None:
            assert embedded
            raise RuntimeError("lease lost")

        with pytest.raises(RuntimeError, match="lease lost"):
            build_index_generation(
                session, document.id, _settings(), lease_guard=reject_lease
            )
        session.rollback()

    with session_factory() as session:
        assert (
            session.scalar(
                select(IndexGeneration.id).where(
                    IndexGeneration.document_id == document_id
                )
            )
            is None
        )


@pytest.mark.parametrize("dimensions", [384, 768])
def test_new_model_configuration_keeps_generations_separate(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    dimensions: int,
) -> None:
    class ChangedAdapter(FakeAdapter):
        revision = "changed-test-revision"

        def __init__(self) -> None:
            self.dimensions = dimensions

        def embed_passages(self, texts: list[str]) -> list[list[float]]:
            return [[1.0] + [0.0] * (self.dimensions - 1) for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            return self.embed_passages([text])[0]

    monkeypatch.setattr(
        retrieval, "get_embedding_adapter", lambda _settings: FakeAdapter()
    )
    with session_factory() as session, session.begin():
        document = _create_document(session)
        first = build_index_generation(session, document.id, _settings())
        assert build_index_generation(session, document.id, _settings()).id == first.id
        original_chunks = list(
            session.scalars(
                select(DocumentChunk.id).where(DocumentChunk.document_id == document.id)
            )
        )
        original_vectors = list(
            session.scalars(
                select(ChunkEmbedding.id).where(
                    ChunkEmbedding.generation_id == first.id
                )
            )
        )
        source_id = document.source_id

    monkeypatch.setattr(
        retrieval, "get_embedding_adapter", lambda _settings: ChangedAdapter()
    )
    changed = _settings().model_copy(
        update={
            "embedding_revision": ChangedAdapter.revision,
            "embedding_dimension": dimensions,
        }
    )
    with session_factory() as session, session.begin():
        second = build_index_generation(session, document.id, changed)
        assert second.id != first.id and second.dimensions == dimensions
        found = search(session, "eligibility", {source_id: 1}, changed, mode="vector")
        assert found["passages"] and all(
            item["generation_id"] == second.id for item in found["passages"]
        )
        assert session.get(IndexGeneration, first.id).status == "ready"
        assert (
            list(
                session.scalars(
                    select(ChunkEmbedding.id).where(
                        ChunkEmbedding.generation_id == first.id
                    )
                )
            )
            == original_vectors
        )
        assert (
            list(
                session.scalars(
                    select(DocumentChunk.id).where(
                        DocumentChunk.document_id == document.id
                    )
                )
            )
            == original_chunks
        )
