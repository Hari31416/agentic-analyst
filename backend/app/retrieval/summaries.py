"""Bounded extractive document summaries with version-pinned provenance.

Summaries in this module are navigation aids. Every sentence is copied from an
original chunk, and callers must retain and present the supporting excerpts.
No model is called by the default implementation.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from collections import Counter, defaultdict
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentChunk, Source, SummaryCache

SummaryScope = Literal["document", "section", "overview"]
ALGORITHM_VERSION = "extractive-summary-v1"
PROMPT_VERSION = "none"
MODEL_ID = "none"
MAX_DOCUMENTS = 10
MAX_CHUNKS_PER_DOCUMENT = 240
MAX_TOTAL_CHUNKS = 1000
MAX_SUPPORT = 12
MAX_SENTENCES = 6
MAX_SUMMARY_CHARS = 2400
MAX_EXCERPT_CHARS = 1200
MAX_ORDINAL_SCAN = 4000


class SummaryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def summarize(
    session: Session,
    source_versions: dict[str, int],
    *,
    scope: SummaryScope = "overview",
    source_id: str | None = None,
    section: str | None = None,
    thematic: bool = False,
) -> dict[str, Any]:
    """Return a bounded cached summary and original supporting chunk excerpts."""
    if scope not in {"document", "section", "overview"}:
        raise SummaryError(
            "invalid_summary_scope", "Choose document, section, or overview."
        )
    if scope in {"document", "section"} and not source_id:
        raise SummaryError("source_required", "Choose a selected document source.")
    if scope == "section" and not (section and section.strip()):
        raise SummaryError("section_required", "Provide a section heading.")
    if scope != "section" and section is not None:
        raise SummaryError(
            "unexpected_section",
            "A section heading is only valid for section summaries.",
        )

    eligible = {
        key: value
        for key, value in source_versions.items()
        if isinstance(key, str)
        and isinstance(value, int)
        and not isinstance(value, bool)
    }
    if source_id is not None:
        if source_id not in eligible:
            raise SummaryError(
                "source_not_selected", "The requested document is not selected."
            )
        eligible = {source_id: eligible[source_id]}
    if not eligible:
        raise SummaryError(
            "no_documents_selected", "Select at least one document source."
        )

    rows = list(
        session.execute(
            select(Document, Source)
            .join(Source, Source.id == Document.source_id)
            .where(
                Document.source_id.in_(list(eligible)),
                Document.source_version.in_(set(eligible.values())),
                Source.version == Document.source_version,
                Document.state == "ready",
                Source.state != "deleted",
                Source.kind.in_(["pdf", "docx", "txt", "md", "html", "pptx"]),
            )
            .order_by(Source.display_name, Source.id)
        ).all()
    )
    # Filter pairwise versions in Python; the SQL IN predicates above are only
    # a coarse bound and must not accidentally pair one source with another's version.
    documents = [
        (doc, src) for doc, src in rows if eligible.get(src.id) == doc.source_version
    ]
    if source_id is not None:
        documents = [(doc, src) for doc, src in documents if src.id == source_id]
    if len(documents) > MAX_DOCUMENTS:
        documents = documents[:MAX_DOCUMENTS]
    if not documents:
        raise SummaryError(
            "documents_not_ready", "No selected document is ready to summarize."
        )

    all_chunks: list[dict[str, Any]] = []
    sampled_documents: list[dict[str, Any]] = []
    remaining = MAX_TOTAL_CHUNKS
    for document, source in documents:
        query = select(DocumentChunk).where(
            DocumentChunk.document_id == document.id,
            DocumentChunk.chunker_version == document.chunker_version,
        )
        if scope == "section":
            normalized_heading = _normalize_heading(section or "")
            # heading is the source's structural label, not generated model text.
            query = query.where(
                func.lower(func.trim(DocumentChunk.heading)) == normalized_heading
            )
        total = int(
            session.scalar(select(func.count()).select_from(query.subquery())) or 0
        )
        if total == 0:
            continue
        take = min(total, MAX_CHUNKS_PER_DOCUMENT, remaining)
        if take <= 0:
            break
        # Read only a bounded list of real ordinals, then sample that list. Section
        # chunk ordinals are not necessarily zero-based or contiguous.
        available_ordinals = list(
            session.scalars(
                query.with_only_columns(DocumentChunk.ordinal)
                .order_by(DocumentChunk.ordinal)
                .limit(MAX_ORDINAL_SCAN)
            )
        )
        take = min(take, len(available_ordinals))
        ordinals = sorted(
            {
                available_ordinals[
                    min(
                        len(available_ordinals) - 1,
                        math.floor((i + 0.5) * len(available_ordinals) / take),
                    )
                ]
                for i in range(take)
            }
        )
        chunks = list(
            session.scalars(
                query.where(DocumentChunk.ordinal.in_(ordinals)).order_by(
                    DocumentChunk.ordinal
                )
            )
        )
        sampled_documents.append(
            {
                "document": document,
                "source": source,
                "chunks": chunks,
                "total_chunks": total,
                "sampled_chunks": len(chunks),
            }
        )
        remaining -= len(chunks)
        for chunk in chunks:
            all_chunks.append(_chunk_record(chunk, document, source))

    if not all_chunks:
        raise SummaryError(
            "no_summary_content", "The requested scope has no extractable chunks."
        )

    source_fingerprints = [
        {
            "source_id": item["source"].id,
            "source_version": item["source"].version,
            "content_hash": item["source"].content_hash,
            "extractor_version": item["document"].extractor_version,
            "chunker_version": item["document"].chunker_version,
            "chunk_ids": [chunk.id for chunk in item["chunks"]],
            "chunk_hashes": [
                hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
                for chunk in item["chunks"]
            ],
            "sampled_chunks": item["sampled_chunks"],
            "total_chunks": item["total_chunks"],
        }
        for item in sampled_documents
    ]
    fingerprint_payload = {
        "scope": scope,
        "section": _normalize_heading(section or "") if section else None,
        "thematic": thematic,
        "sources": source_fingerprints,
        "algorithm_version": ALGORITHM_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model_id": MODEL_ID,
        "sampling_limits": [MAX_DOCUMENTS, MAX_CHUNKS_PER_DOCUMENT, MAX_TOTAL_CHUNKS],
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    cached = session.scalar(
        select(SummaryCache).where(SummaryCache.fingerprint == fingerprint)
    )
    if cached is not None:
        return {
            **deepcopy(cached.payload),
            "cache": {"hit": True, "fingerprint": fingerprint},
        }

    selected = _select_sentences(all_chunks)
    summary = _join_sentences(selected, MAX_SUMMARY_CHARS)
    support = _support_records(selected, all_chunks)
    truncated = any(
        item["sampled_chunks"] < item["total_chunks"] for item in sampled_documents
    )
    result: dict[str, Any] = {
        "scope": scope,
        "summary": summary,
        "thematic": [],
        "supporting_passages": support,
        "documents": [
            {
                "source_id": item["source"].id,
                "source_version": item["source"].version,
                "name": item["source"].display_name,
                "sampled_chunks": item["sampled_chunks"],
                "total_chunks": item["total_chunks"],
            }
            for item in sampled_documents
        ],
        "truncated": truncated or len(documents) == MAX_DOCUMENTS,
        "method": {
            "algorithm": ALGORITHM_VERSION,
            "model": MODEL_ID,
            "prompt_version": PROMPT_VERSION,
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": 0,
            "cost_currency": "USD",
        },
        "cache": {"hit": False, "fingerprint": fingerprint},
        "evidence_policy": "Summary text is extractive navigation only; use supporting original passages for factual claims.",
    }
    if thematic and scope == "overview":
        result["thematic"] = _thematic_summaries(all_chunks)
    cache = SummaryCache(fingerprint=fingerprint, scope=scope, payload=result)
    session.add(cache)
    session.flush()
    return result


def _chunk_record(
    chunk: DocumentChunk, document: Document, source: Source
) -> dict[str, Any]:
    return {
        "chunk_id": chunk.id,
        "document_id": document.id,
        "source_id": source.id,
        "source_version": source.version,
        "document_version": document.source_version,
        "source_name": source.display_name,
        "heading": chunk.heading,
        "location": chunk.location or {},
        "text": chunk.text,
        "ordinal": chunk.ordinal,
    }


def _select_sentences(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sentences: list[dict[str, Any]] = []
    df: Counter[str] = Counter()
    for chunk in chunks:
        seen = set()
        for sentence in _sentences(chunk["text"]):
            terms = _terms(sentence)
            seen.update(terms)
            if 24 <= len(sentence) <= MAX_EXCERPT_CHARS:
                sentences.append({"text": sentence, "chunk": chunk, "terms": terms})
        df.update(seen)
    if not sentences:
        return []
    count = len(sentences)
    scored = []
    for position, item in enumerate(sentences):
        terms = item["terms"]
        informativeness = sum(1 + math.log1p(df[term]) for term in terms)
        length_penalty = max(0.55, min(1.0, 120 / max(120, len(item["text"]))))
        heading_bonus = 1.15 if item["chunk"].get("heading") else 1.0
        scored.append(
            (informativeness * length_penalty * heading_bonus, position, item)
        )
    chosen: list[dict[str, Any]] = []
    chosen_terms: set[str] = set()
    chosen_chunks: set[str] = set()
    while scored and len(chosen) < MAX_SENTENCES:
        scored.sort(
            key=lambda row: (
                -row[0] * (1 + 0.15 * len(row[2]["terms"] - chosen_terms)),
                row[1],
            )
        )
        _, _, item = scored.pop(0)
        chunk_id = item["chunk"]["chunk_id"]
        if chunk_id in chosen_chunks and len(chosen_chunks) < min(
            MAX_SUPPORT, len(chunks)
        ):
            continue
        chosen.append(item)
        chosen_terms.update(item["terms"])
        chosen_chunks.add(chunk_id)
    chosen.sort(
        key=lambda item: (
            item["chunk"]["source_id"],
            item["chunk"]["ordinal"],
            item["text"],
        )
    )
    return chosen


def _support_records(
    selected: list[dict[str, Any]], chunks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    del chunks
    return [
        {
            "chunk_id": item["chunk"]["chunk_id"],
            "document_id": item["chunk"]["document_id"],
            "source_id": item["chunk"]["source_id"],
            "source_version": item["chunk"]["source_version"],
            "document_version": item["chunk"]["document_version"],
            "source_name": item["chunk"]["source_name"],
            "heading": item["chunk"]["heading"],
            "location": item["chunk"]["location"],
            # Summary sentences are copied from the original text; retaining the
            # exact sentence here makes it inspectable even when later in a chunk.
            "excerpt": item["text"],
            "truncated": False,
        }
        for item in selected[:MAX_SUPPORT]
    ]


def _thematic_summaries(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        heading = (
            " ".join((chunk.get("heading") or "").split()) or "Unsectioned content"
        )
        groups[heading].append(chunk)
    ranked = sorted(
        groups.items(), key=lambda entry: (-len(entry[1]), entry[0].casefold())
    )[:6]
    output = []
    for heading, items in ranked:
        selection = _select_sentences(items)[:3]
        output.append(
            {
                "theme": heading[:160],
                "summary": _join_sentences(selection, 800),
                "supporting_chunk_ids": list(
                    dict.fromkeys(item["chunk"]["chunk_id"] for item in selection)
                )[:MAX_SUPPORT],
                "supporting_passages": _support_records(selection, items),
            }
        )
    return output


def _join_sentences(selected: list[dict[str, Any]], max_chars: int) -> str:
    result = " ".join(item["text"] for item in selected)
    if len(result) <= max_chars:
        return result
    clipped = result[:max_chars].rsplit(" ", 1)[0]
    return clipped.rstrip(" .;,:;:") + "…"


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?।॥])\s+|\n+", text.strip())
    # Preserve sentence words and punctuation; only trim surrounding whitespace.
    return [part.strip() for part in parts if part.strip()]


def _terms(text: str) -> set[str]:
    return {
        term.casefold()
        for term in re.findall(r"[^\W_]+", text, flags=re.UNICODE)
        if len(term) > 2 and not term.isdecimal()
    }


def _normalize_heading(value: str) -> str:
    return " ".join(value.casefold().split())
