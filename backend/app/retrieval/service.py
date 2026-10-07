"""Version-scoped document indexing, retrieval, and passage expansion."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import unicodedata
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import and_, delete, func, literal, literal_column, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.config import Settings
from app.db.models import (
    ChunkEmbedding,
    Document,
    DocumentChunk,
    IndexGeneration,
    Source,
)
from app.retrieval.embedding import (
    EmbeddingAdapter,
    EmbeddingUnavailable,
    get_embedding_adapter,
)

RetrievalMode = Literal["text", "vector", "hybrid"]
_RRF_K = 60
_MAX_LIMIT = 20
_MAX_CANDIDATES = 80
_MAX_QUERY_CHARS = 2_000
_DEGRADED_STATUS = "degraded"


class RetrievalError(RuntimeError):
    """Safe retrieval failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def build_index_generation(
    session: Session,
    document_id: str,
    settings: Settings,
    lease_guard: Callable[[Session], None] | None = None,
) -> IndexGeneration:
    """Embed persisted chunks into an idempotent, version-pinned generation.

    The caller owns the session transaction. This function flushes rows but
    never commits, so ingestion can retain job-lease checks around publication.
    Missing model assets create a degraded lexical generation without fake
    vectors; inference failures create a degraded lexical generation. Embedding
    inference runs before any database writes. An optional lease guard runs
    between batches and immediately before publication.
    """
    document = session.get(Document, document_id)
    if document is None:
        raise RetrievalError(
            "document_not_found", "The selected document is unavailable."
        )
    source = session.get(Source, document.source_id)
    if source is None:
        raise RetrievalError("source_not_found", "The document source is unavailable.")
    chunks = list(
        session.scalars(
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id == document.id,
                DocumentChunk.chunker_version == document.chunker_version,
            )
            .order_by(DocumentChunk.ordinal, DocumentChunk.id)
        )
    )
    if not chunks:
        raise RetrievalError(
            "document_has_no_chunks", "The document has no searchable chunks."
        )

    model_id = settings.embedding_model or "unconfigured"
    content_hash = source.content_hash or _chunks_hash(chunks)
    fingerprint_payload = {
        "document_id": document.id,
        "source_version": document.source_version,
        "content_sha256": content_hash,
        "extractor_version": document.extractor_version,
        "chunker_version": document.chunker_version,
        "model_id": model_id,
        "model_revision": settings.embedding_revision,
        "dimensions": settings.embedding_dimension,
        "vector_metric": "cosine",
        "chunk_ids": [chunk.id for chunk in chunks],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    existing = session.scalar(
        select(IndexGeneration).where(IndexGeneration.fingerprint == fingerprint)
    )
    if existing is not None and existing.status == "ready":
        if document.index_generation_id != existing.id:
            if lease_guard is not None:
                lease_guard(session)
            document.index_generation_id = existing.id
            session.flush()
        return existing
    adapter: EmbeddingAdapter | None
    embedding_failure: EmbeddingUnavailable | None = None
    try:
        adapter = get_embedding_adapter(settings)
    except EmbeddingUnavailable as error:
        adapter = None
        embedding_failure = error

    if adapter is not None:
        # Fingerprint the actual pinned model revision, even when settings omitted
        # it and the verified on-disk manifest supplies the revision.
        fingerprint_payload["model_revision"] = adapter.revision
        adapter_name = getattr(adapter, "adapter_name", "fastembed-e5-custom-v1")
        adapter_type = getattr(adapter, "adapter_type", "fastembed-local")
        fingerprint_payload["embedding_adapter_version"] = adapter_name
        if "sentence-transformers" in adapter_type:
            try:
                fingerprint_payload["sentence_transformers_version"] = (
                    importlib.metadata.version("sentence_transformers")
                )
            except Exception:
                fingerprint_payload["sentence_transformers_version"] = "unknown"
        else:
            try:
                fingerprint_payload["fastembed_version"] = importlib.metadata.version(
                    "fastembed"
                )
            except Exception:
                fingerprint_payload["fastembed_version"] = "unknown"
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        exact_generation = session.scalar(
            select(IndexGeneration).where(IndexGeneration.fingerprint == fingerprint)
        )
        existing = exact_generation or (
            existing
            if existing is not None and existing.status == _DEGRADED_STATUS
            else None
        )
        if existing is not None and existing.status == "ready":
            if document.index_generation_id != existing.id:
                if lease_guard is not None:
                    lease_guard(session)
                document.index_generation_id = existing.id
                session.flush()
            return existing

    vectors_by_chunk: list[tuple[DocumentChunk, list[float]]] = []
    if adapter is not None:
        passage_texts = [_passage_text(chunk) for chunk in chunks]
        batch_size = settings.embedding_batch_size
        for offset in range(0, len(passage_texts), batch_size):
            if lease_guard is not None:
                lease_guard(session)
            chunk_batch = chunks[offset : offset + batch_size]
            text_batch = passage_texts[offset : offset + batch_size]
            try:
                has_multimodal = hasattr(adapter, "embed_multimodal") and any(
                    bool(chunk.location and chunk.location.get("image_key"))
                    for chunk in chunk_batch
                )
                if has_multimodal:
                    from app.storage.factory import get_storage

                    storage = get_storage(settings)
                    items: list[dict[str, Any]] = []
                    for chunk, text in zip(chunk_batch, text_batch, strict=True):
                        image_key = (
                            chunk.location.get("image_key") if chunk.location else None
                        )
                        img_bytes = storage.read(image_key) if image_key else None
                        items.append({"text": text, "image_bytes": img_bytes})
                    vectors = adapter.embed_multimodal(items)
                else:
                    vectors = adapter.embed_passages(text_batch)
                if len(vectors) != len(text_batch):
                    raise EmbeddingUnavailable(
                        "embedding_output_mismatch",
                        "The embedding model returned an incomplete batch.",
                    )
                if any(len(vector) != adapter.dimensions for vector in vectors):
                    raise EmbeddingUnavailable(
                        "embedding_dimension_mismatch",
                        "The embedding vector has an unexpected dimension.",
                    )
                vectors_by_chunk.extend(zip(chunk_batch, vectors, strict=True))
            except EmbeddingUnavailable as error:
                embedding_failure = error
                break
            except Exception:
                embedding_failure = EmbeddingUnavailable(
                    "embedding_inference_failed", "Local embedding inference failed."
                )
                break

    if lease_guard is not None:
        lease_guard(session)

    if embedding_failure is not None:
        if existing is not None:
            return _mark_degraded(existing, document, embedding_failure, session)
        generation = _new_generation(
            session,
            document,
            dimensions=settings.embedding_dimension,
            fingerprint=fingerprint,
            model_id=model_id,
            content_hash=content_hash,
            status=_DEGRADED_STATUS,
            config={
                **fingerprint_payload,
                "embedding_status": "unavailable",
                "error_code": embedding_failure.code,
            },
        )
        document.index_generation_id = generation.id
        session.flush()
        return generation
    assert adapter is not None

    if existing is None:
        generation = _new_generation(
            session,
            document,
            dimensions=settings.embedding_dimension,
            fingerprint=fingerprint,
            model_id=adapter.model_id,
            content_hash=content_hash,
            status="building",
            config={
                **fingerprint_payload,
                "model_revision": adapter.revision,
                "embedding_status": "building",
                "adapter": getattr(adapter, "adapter_type", "fastembed-local"),
            },
        )
    else:
        generation = existing
        generation.status = "building"
        generation.fingerprint = fingerprint
        generation.config_json = {
            **fingerprint_payload,
            "model_revision": adapter.revision,
            "embedding_status": "building",
            "adapter": getattr(adapter, "adapter_type", "fastembed-local"),
        }
        session.execute(
            delete(ChunkEmbedding).where(ChunkEmbedding.generation_id == generation.id)
        )
    document.index_generation_id = generation.id
    session.flush()
    for chunk, vector in vectors_by_chunk:
        session.add(
            ChunkEmbedding(
                generation_id=generation.id,
                chunk_id=chunk.id,
                embedding=vector,
            )
        )
    session.flush()

    generation.status = "ready"
    generation.ready_at = datetime.now(timezone.utc)
    generation.config_json = {
        **generation.config_json,
        "embedding_status": "ready",
        "embedded_chunk_count": len(vectors_by_chunk),
    }
    session.flush()
    return generation


def search(
    session: Session,
    query: str,
    source_versions: dict[str, int],
    settings: Settings,
    mode: RetrievalMode = "hybrid",
    limit: int = 5,
    context_budget: int = 8_000,
    *,
    lexical_any: bool = False,
    candidate_budget: int | None = None,
) -> dict[str, Any]:
    """Search only selected source versions and fuse lexical/dense candidates."""
    cleaned = _clean_query(query)
    if mode not in {"text", "vector", "hybrid"}:
        raise RetrievalError("invalid_mode", "Retrieval mode is unsupported.")
    if isinstance(limit, bool) or not 1 <= limit <= _MAX_LIMIT:
        raise RetrievalError("invalid_limit", "Result limit must be from 1 to 20.")
    context_budget = _bounded_context(context_budget, settings)
    if not source_versions:
        if mode == "vector":
            raise RetrievalError(
                "no_sources_selected", "Select a document source to search."
            )
        return _empty_search(mode, trace=[{"stage": "scope", "status": "empty"}])

    candidate_limit = min(max(limit * 4, limit), _MAX_CANDIDATES)
    if candidate_budget is not None:
        if isinstance(candidate_budget, bool) or candidate_budget < 2:
            raise RetrievalError(
                "invalid_candidate_budget",
                "Candidate budget must allow at least two candidates.",
            )
        candidate_limit = min(
            candidate_limit, candidate_budget // (2 if mode == "hybrid" else 1)
        )
    lexical: list[dict[str, Any]] = []
    dense: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []
    if mode in {"text", "hybrid"}:
        lexical = _lexical_candidates(
            session, cleaned, source_versions, candidate_limit, any_terms=lexical_any
        )
        trace.append(
            {"stage": "lexical", "status": "ready", "candidate_count": len(lexical)}
        )

    dense_error: EmbeddingUnavailable | None = None
    adapter: EmbeddingAdapter | None = None
    incomplete_dense_scope = False
    if mode in {"vector", "hybrid"}:
        try:
            adapter = get_embedding_adapter(settings)
            query_vector = adapter.embed_query(cleaned)
            selected_document_count, matching_generation_count = _dense_coverage(
                session, adapter, source_versions
            )
            incomplete_dense_scope = matching_generation_count < selected_document_count
            dense = _dense_candidates(
                session,
                query_vector,
                adapter,
                settings,
                source_versions,
                candidate_limit,
            )
            trace.append(
                {
                    "stage": "dense",
                    "status": "ready" if dense else "no_matching_generation",
                    "model_id": adapter.model_id,
                    "model_revision": adapter.revision,
                    "dimensions": adapter.dimensions,
                    "candidate_count": len(dense),
                    "selected_document_count": selected_document_count,
                    "matching_generation_count": matching_generation_count,
                }
            )
            if not dense or matching_generation_count == 0:
                dense_error = EmbeddingUnavailable(
                    "embedding_unavailable",
                    "No ready embedding generation matches the selected source versions.",
                )
            elif incomplete_dense_scope:
                trace[-1]["status"] = "partial"
        except EmbeddingUnavailable as error:
            dense_error = error
            trace.append(
                {"stage": "dense", "status": "unavailable", "code": error.code}
            )

    if mode == "vector" and dense_error is not None:
        raise RetrievalError(dense_error.code, dense_error.message)
    degraded = mode in {"hybrid", "vector"} and (
        dense_error is not None or incomplete_dense_scope
    )
    if mode == "text":
        ranked = _rank_single(lexical, "lexical")
        actual_mode = "text"
    elif mode == "vector":
        ranked = _rank_single(dense, "dense")
        actual_mode = "vector"
    elif degraded and mode == "hybrid" and dense_error is not None:
        ranked = _rank_single(lexical, "lexical")
        actual_mode = "text"
        trace.append(
            {"stage": "fusion", "status": "degraded", "reason": dense_error.code}
        )
    elif degraded and mode == "hybrid":
        ranked = _fuse(lexical, dense)
        actual_mode = "hybrid"
        trace.append(
            {
                "stage": "fusion",
                "status": "partial",
                "reason": "generation_incomplete",
                "rrf_k": _RRF_K,
            }
        )
    elif degraded:
        ranked = _rank_single(dense, "dense")
        actual_mode = "vector"
        trace.append(
            {
                "stage": "fusion",
                "status": "partial",
                "reason": "generation_incomplete",
            }
        )
    else:
        ranked = _fuse(lexical, dense)
        actual_mode = "hybrid"
        trace.append({"stage": "fusion", "status": "ready", "rrf_k": _RRF_K})

    ranked = _dedupe_adjacent(ranked)
    passages = _search_passages(ranked[:limit], context_budget)
    return {
        "mode": actual_mode,
        "requested_mode": mode,
        "degraded": degraded,
        "trace": trace,
        "passages": passages,
    }


def get_passage(
    session: Session,
    chunk_id: str,
    source_versions: dict[str, int],
    settings: Settings,
    neighbors: int = 1,
    context_budget: int = 8_000,
) -> dict[str, Any]:
    """Read one cited chunk and a bounded window from its source version."""
    if not source_versions:
        raise RetrievalError("no_sources_selected", "Select a document source first.")
    if isinstance(neighbors, bool) or not 0 <= neighbors <= 2:
        raise RetrievalError(
            "invalid_neighbors", "Neighbor window must be from 0 to 2."
        )
    context_budget = _bounded_context(context_budget, settings)
    selected = _source_version_predicate(source_versions)
    center = session.execute(
        select(DocumentChunk, Document, Source)
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(Source, Source.id == Document.source_id)
        .where(
            DocumentChunk.id == chunk_id,
            selected,
            Document.state == "ready",
        )
    ).first()
    if center is None:
        raise RetrievalError(
            "passage_not_found", "The selected passage is unavailable."
        )
    center_chunk, document, source = center
    surrounding = list(
        session.execute(
            select(DocumentChunk)
            .where(
                DocumentChunk.document_id == document.id,
                DocumentChunk.chunker_version == document.chunker_version,
                DocumentChunk.ordinal.between(
                    max(0, center_chunk.ordinal - neighbors),
                    center_chunk.ordinal + neighbors,
                ),
            )
            .order_by(DocumentChunk.ordinal)
        ).scalars()
    )
    passages = _bound_passages(
        [_chunk_view(item, document, source) for item in surrounding], context_budget
    )
    center_view = _chunk_view(center_chunk, document, source)
    center_excerpt = center_view["excerpt"]
    center_view["excerpt"] = center_excerpt[:context_budget]
    center_view["truncated"] = len(center_excerpt) > context_budget
    combined, truncated = _combine_passages(passages, context_budget)
    return {
        **center_view,
        "generation_id": document.index_generation_id,
        "context": combined,
        "neighbors": passages,
        "truncated": truncated,
    }


def _new_generation(
    session: Session,
    document: Document,
    *,
    dimensions: int,
    fingerprint: str,
    model_id: str,
    content_hash: str,
    status: str,
    config: dict[str, Any],
) -> IndexGeneration:
    generation = IndexGeneration(
        document_id=document.id,
        source_version=document.source_version,
        content_sha256=content_hash,
        extractor_version=document.extractor_version,
        chunker_version=document.chunker_version,
        model_id=model_id,
        dimensions=dimensions,
        vector_metric="cosine",
        status=status,
        config_json=config,
        fingerprint=fingerprint,
        ready_at=(datetime.now(timezone.utc) if status == _DEGRADED_STATUS else None),
    )
    session.add(generation)
    session.flush()
    return generation


def _mark_degraded(
    generation: IndexGeneration,
    document: Document,
    error: EmbeddingUnavailable,
    session: Session,
) -> IndexGeneration:
    generation.status = _DEGRADED_STATUS
    generation.config_json = {
        **generation.config_json,
        "embedding_status": "unavailable",
        "error_code": error.code,
    }
    generation.ready_at = datetime.now(timezone.utc)
    document.index_generation_id = generation.id
    session.flush()
    return generation


def _chunks_hash(chunks: list[DocumentChunk]) -> str:
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk.id.encode())
        digest.update(b"\x00")
        digest.update(chunk.text.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _passage_text(chunk: DocumentChunk) -> str:
    title = chunk.heading.strip() if chunk.heading and chunk.heading.strip() else "none"
    return f"title: {title} | text: {chunk.text.strip()}"


def _clean_query(query: str) -> str:
    value = unicodedata.normalize("NFKC", query).strip()
    value = re.sub(r"\s+", " ", value)
    if not value:
        raise RetrievalError("empty_query", "Enter a search query.")
    if len(value) > _MAX_QUERY_CHARS:
        raise RetrievalError("query_too_long", "Search query is too long.")
    return value


def _source_version_predicate(source_versions: dict[str, int]) -> Any:
    if not source_versions or len(source_versions) > 100:
        return False
    if any(
        not isinstance(source_id, str)
        or not source_id
        or isinstance(version, bool)
        or not isinstance(version, int)
        or version < 1
        for source_id, version in source_versions.items()
    ):
        raise RetrievalError(
            "invalid_source_versions", "Selected source versions are invalid."
        )
    return or_(
        *(
            and_(Document.source_id == source_id, Document.source_version == version)
            for source_id, version in sorted(source_versions.items())
        )
    )


def _eligible_generation(source_versions: dict[str, int]) -> Any:
    return (
        Document.index_generation_id == IndexGeneration.id,
        IndexGeneration.document_id == Document.id,
        IndexGeneration.source_version == Document.source_version,
        IndexGeneration.status.in_(["ready", _DEGRADED_STATUS]),
        Document.state == "ready",
        _source_version_predicate(source_versions),
    )


def _lexical_candidates(
    session: Session,
    query: str,
    source_versions: dict[str, int],
    limit: int,
    *,
    any_terms: bool = False,
    strict_budget: bool = False,
) -> list[dict[str, Any]]:
    combined = (
        func.coalesce(DocumentChunk.heading, "")
        + literal(" ")
        + DocumentChunk.normalized_text
    )
    candidates: dict[str, dict[str, Any]] = {}
    for config, language_filter, config_limit in (
        (
            "english",
            DocumentChunk.language.ilike("en%"),
            (limit + 1) // 2 if strict_budget else limit,
        ),
        (
            "simple",
            ~DocumentChunk.language.ilike("en%"),
            limit // 2 if strict_budget else limit,
        ),
    ):
        config_expression: ColumnElement[Any] = literal_column(f"'{config}'::regconfig")
        ts_query = (
            func.websearch_to_tsquery(
                config_expression,
                " OR ".join(re.findall(r"[^\W_]+", query, re.UNICODE)),
            )
            if any_terms
            else func.plainto_tsquery(config_expression, query)
        )
        vector = func.to_tsvector(config_expression, combined)
        score = func.ts_rank_cd(vector, ts_query)
        rows = session.execute(
            select(
                DocumentChunk, Document, Source, IndexGeneration, score.label("score")
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            .join(Source, Source.id == Document.source_id)
            .join(IndexGeneration, Document.index_generation_id == IndexGeneration.id)
            .where(
                *_eligible_generation(source_versions),
                language_filter,
                vector.op("@@")(ts_query),
            )
            .order_by(score.desc(), DocumentChunk.id)
            .limit(config_limit)
        )
        for chunk, document, source, generation, raw_score in rows:
            item = _candidate(chunk, document, source, generation)
            prior = candidates.get(chunk.id)
            numeric_score = float(raw_score or 0.0)
            if prior is None or numeric_score > prior["_score"]:
                item["_score"] = numeric_score
                candidates[chunk.id] = item
    ordered = sorted(
        candidates.values(), key=lambda item: (-item["_score"], item["chunk_id"])
    )[:limit]
    for rank, item in enumerate(ordered, start=1):
        item["lexical_rank"] = rank
        item["lexical_score"] = item.pop("_score")
    return ordered


def _dense_coverage(
    session: Session,
    adapter: EmbeddingAdapter,
    source_versions: dict[str, int],
) -> tuple[int, int]:
    scope = _source_version_predicate(source_versions)
    selected_count = int(
        session.scalar(
            select(func.count(Document.id)).where(
                Document.state == "ready",
                scope,
            )
        )
        or 0
    )
    revision = IndexGeneration.config_json["model_revision"].as_string()
    matching_count = int(
        session.scalar(
            select(func.count(func.distinct(Document.id)))
            .join(IndexGeneration, Document.index_generation_id == IndexGeneration.id)
            .where(
                Document.state == "ready",
                scope,
                IndexGeneration.document_id == Document.id,
                IndexGeneration.source_version == Document.source_version,
                IndexGeneration.status == "ready",
                IndexGeneration.model_id == adapter.model_id,
                IndexGeneration.dimensions == adapter.dimensions,
                IndexGeneration.vector_metric == "cosine",
                revision == adapter.revision,
            )
        )
        or 0
    )
    return selected_count, matching_count


def _dense_candidates(
    session: Session,
    query_vector: list[float],
    adapter: EmbeddingAdapter,
    settings: Settings,
    source_versions: dict[str, int],
    limit: int,
) -> list[dict[str, Any]]:
    distance = ChunkEmbedding.embedding.cosine_distance(query_vector)
    revision_predicate = (
        IndexGeneration.config_json["model_revision"].as_string() == adapter.revision
    )
    rows = session.execute(
        select(
            DocumentChunk,
            Document,
            Source,
            IndexGeneration,
            distance.label("distance"),
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(Source, Source.id == Document.source_id)
        .join(IndexGeneration, Document.index_generation_id == IndexGeneration.id)
        .join(ChunkEmbedding, ChunkEmbedding.generation_id == IndexGeneration.id)
        .where(
            *_eligible_generation(source_versions),
            IndexGeneration.status == "ready",
            IndexGeneration.model_id == adapter.model_id,
            IndexGeneration.dimensions == adapter.dimensions,
            IndexGeneration.vector_metric == "cosine",
            revision_predicate,
            ChunkEmbedding.chunk_id == DocumentChunk.id,
        )
        .order_by(distance.asc(), DocumentChunk.id)
        .limit(limit)
    )
    ranked = []
    for rank, (chunk, document, source, generation, raw_distance) in enumerate(
        rows, start=1
    ):
        item = _candidate(chunk, document, source, generation)
        item["dense_rank"] = rank
        item["dense_score"] = 1.0 - float(raw_distance)
        ranked.append(item)
    return ranked


def _candidate(
    chunk: DocumentChunk,
    document: Document,
    source: Source,
    generation: IndexGeneration,
) -> dict[str, Any]:
    return {
        "chunk": chunk,
        "document": document,
        "source": source,
        "generation": generation,
        "chunk_id": chunk.id,
        "document_id": document.id,
        "source_id": source.id,
        "source_version": document.source_version,
    }


def _rank_single(candidates: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    result = []
    for rank, candidate in enumerate(candidates, start=1):
        item = dict(candidate)
        item["rank"] = rank
        item["score"] = item.get(f"{kind}_score", 0.0)
        item["rrf_score"] = None
        result.append(item)
    return result


def _fuse(
    lexical: list[dict[str, Any]], dense: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}
    for kind, candidates in (("lexical", lexical), ("dense", dense)):
        rank_key = f"{kind}_rank"
        for candidate in candidates:
            item = fused.setdefault(candidate["chunk_id"], dict(candidate))
            item[rank_key] = candidate[rank_key]
            item["rrf_score"] = item.get("rrf_score", 0.0) + 1.0 / (
                _RRF_K + candidate[rank_key]
            )
    ranked = sorted(
        fused.values(), key=lambda item: (-item["rrf_score"], item["chunk_id"])
    )
    for rank, item in enumerate(ranked, start=1):
        item["rank"] = rank
        item["score"] = item["rrf_score"]
    return ranked


def _dedupe_adjacent(ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop overlapping neighboring chunks while retaining distinct passages."""
    kept: list[dict[str, Any]] = []
    for item in ranked:
        chunk: DocumentChunk = item["chunk"]
        duplicate = False
        for selected in kept:
            other: DocumentChunk = selected["chunk"]
            if other.document_id != chunk.document_id:
                continue
            if other.id == chunk.previous_id or other.id == chunk.next_id:
                earlier, later = (
                    (other, chunk) if other.ordinal < chunk.ordinal else (chunk, other)
                )
                duplicate = _has_chunk_overlap(earlier.text, later.text)
                if duplicate:
                    break
        if not duplicate:
            item["rank"] = len(kept) + 1
            kept.append(item)
    return kept


def _has_chunk_overlap(earlier: str, later: str) -> bool:
    left = earlier.rstrip().casefold()
    right = later.lstrip().casefold()
    maximum = min(len(left), len(right), 512)
    for length in range(maximum, 47, -16):
        if left.endswith(right[:length]):
            return True
    return False


def _search_passages(
    ranked: list[dict[str, Any]], context_budget: int
) -> list[dict[str, Any]]:
    if not ranked:
        return []
    budget_each = max(1, context_budget // len(ranked))
    result = []
    for item in ranked:
        view = _chunk_view(item["chunk"], item["document"], item["source"])
        excerpt = view["excerpt"]
        truncated = len(excerpt) > budget_each
        view["excerpt"] = excerpt[:budget_each]
        view["truncated"] = truncated
        view.update(
            {
                "generation_id": item["generation"].id,
                "rank": item["rank"],
                "score": item["score"],
                "lexical_rank": item.get("lexical_rank"),
                "dense_rank": item.get("dense_rank"),
                "lexical_score": item.get("lexical_score"),
                "dense_score": item.get("dense_score"),
                "rrf_score": item.get("rrf_score"),
            }
        )
        result.append(view)
    return result


def _chunk_view(
    chunk: DocumentChunk, document: Document, source: Source
) -> dict[str, Any]:
    loc = dict(chunk.location or {})
    return {
        "chunk_id": chunk.id,
        "document_id": document.id,
        "source_id": source.id,
        "source_version": document.source_version,
        "display_name": source.display_name,
        "excerpt": chunk.text,
        "heading": chunk.heading,
        "location": loc,
        "language": chunk.language,
        "block_ids": list(chunk.block_ids or []),
        "image_key": loc.get("image_key"),
    }


def _combine_passages(passages: list[dict[str, Any]], budget: int) -> tuple[str, bool]:
    parts: list[str] = []
    remaining = budget
    truncated = False
    for item in passages:
        text = str(item["excerpt"])
        if not text:
            continue
        heading = item.get("heading")
        prefix = f"{heading}\n" if heading else ""
        content = prefix + text
        if len(content) > remaining:
            content = content[:remaining]
            truncated = True
        if content:
            parts.append(content)
            remaining -= len(content) + 2
        if remaining <= 0:
            truncated = True
            break
    return "\n\n".join(parts), truncated


def _bound_passages(
    passages: list[dict[str, Any]], budget: int
) -> list[dict[str, Any]]:
    if not passages:
        return []
    per_passage = max(1, budget // len(passages))
    bounded = []
    for passage in passages:
        item = dict(passage)
        excerpt = str(item["excerpt"])
        item["excerpt"] = excerpt[:per_passage]
        item["truncated"] = len(excerpt) > per_passage
        bounded.append(item)
    return bounded


def _bounded_context(value: int, settings: Settings) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RetrievalError(
            "invalid_context_budget", "Context budget must be positive."
        )
    return min(value, settings.max_context_characters, 40_000)


def _empty_search(mode: RetrievalMode, trace: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "mode": mode,
        "requested_mode": mode,
        "degraded": mode == "hybrid",
        "trace": trace,
        "passages": [],
    }
