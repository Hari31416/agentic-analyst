"""Bounded retrieval stages; all passages keep their original citation anchors."""

from __future__ import annotations

import re
import time
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import DocumentChunk, Document, Source
from app.retrieval.service import (
    RetrievalError,
    RetrievalMode,
    _chunk_view,
    _source_version_predicate,
    search,
)

PIPELINE_VERSION = "advanced-retrieval-v1"
MAX_VARIANTS = 3
MAX_CANDIDATES = 60
# Removal only affects an additional query. Original queries always execute.
_STOP = set(
    "what which are is the a an of for in and to does please explain tell me about क्या कौन है हैं के की का में और बताएं नियम".split()
)


def query_variants(
    query: str,
    *,
    previous_query: str | None = None,
    variants: list[str] | None = None,
    aliases: dict[str, list[str]] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    original = query.strip()
    result = [original]
    trace: list[dict[str, Any]] = []
    # Additive rewriting never replaces the user's wording, names or numbers.
    if previous_query and re.search(
        r"\b(it|that|those|same|they|its)\b|उस|वही|इसके", original, re.I
    ):
        combined = original + " " + previous_query.strip()
        if len(combined) <= 2000:
            result.append(combined)
            trace.append(
                {
                    "stage": "rewrite",
                    "status": "ready",
                    "method": "additive-user-context-v1",
                    "original": original,
                    "query": combined,
                }
            )
    if not trace:
        trace.append({"stage": "rewrite", "status": "skipped", "original": original})
    tokens = original.split()
    keyword = " ".join(
        token for token in tokens if token.casefold().strip("?,।.") not in _STOP
    )
    if keyword and keyword != original:
        result.append(keyword)
    for key, values in (aliases or {}).items():
        if key.casefold() in original.casefold():
            expanded = original + " " + " ".join(values)
            if len(expanded) <= 2000:
                result.append(expanded)
    protected = set(re.findall(r'\b\w*\d\w*\b|"[^"]+"|\b[A-Z][\w-]*\b', original))
    # Two literal keyword subsets let lexical retrieval recover passages when
    # some query words are absent. Required names/identifiers stay in both.
    keywords = keyword.split()
    if len(keywords) > 4:
        for parity in (0, 1):
            subset = " ".join(
                word
                for i, word in enumerate(keywords)
                if i % 2 == parity or any(term in word for term in protected)
            )
            if subset:
                result.append(subset)
    rejected = 0
    for variant in variants or []:
        if (
            not variant.strip()
            or len(variant) > 2000
            or any(term not in variant for term in protected)
        ):
            rejected += 1
        else:
            result.insert(1, variant.strip())
    unique = list(dict.fromkeys(result))[:MAX_VARIANTS]
    trace.append(
        {
            "stage": "expansion",
            "status": "fallback" if rejected else "ready",
            "rejected_variants": rejected,
            "queries": unique,
            "method": "keyword-alias-v1",
            "model_calls": 0,
        }
    )
    return unique, trace


def fuse_variants(
    results: list[dict[str, Any]], consensus: bool = False
) -> list[dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}
    for variant, result in enumerate(results):
        variant = result.get("variant_index", variant)
        for rank, passage in enumerate(result["passages"], 1):
            item = fused.setdefault(
                passage["chunk_id"], {**passage, "score": 0.0, "variants": []}
            )
            item["score"] += 1 / (60 + rank)
            item["variants"].append({"variant": variant, "rank": rank})
    if consensus:
        for item in fused.values():
            item["score"] *= 1 + 0.1 * (len(item["variants"]) - 1)
    ranked = sorted(fused.values(), key=lambda item: (-item["score"], item["chunk_id"]))
    for rank, item in enumerate(ranked, 1):
        item["fusion_rank"] = rank
    return ranked


def compress_excerpt(text: str, query: str) -> str:
    """Extract matching sentences verbatim. This never overwrites original text."""
    terms = {word.casefold().strip("?,।.") for word in query.split()} - _STOP
    sentences = re.split(r"(?<=[.!?।])\s+|\n+", text)
    selected = [
        sentence
        for sentence in sentences
        if any(term in sentence.casefold() for term in terms)
    ]
    return "\n".join(selected) if selected else text


def assemble_context(
    session: Session,
    candidates: list[dict[str, Any]],
    versions: dict[str, int],
    *,
    limit: int,
    budget: int,
    token_budget: int,
    expand: bool,
    compress: bool,
    query: str,
    diversity: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # Round-robin the selected documents before taking further hits from one.
    chosen: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    remaining = list(candidates)
    while remaining and len(chosen) < limit:
        index = min(
            range(len(remaining)),
            key=lambda i: (counts[remaining[i]["document_id"]], i),
        )
        item = remaining.pop(index if diversity else 0)
        chosen.append(dict(item))
        counts[item["document_id"]] += 1
    expanded: list[dict[str, Any]] = []
    if expand:
        for item in chosen:
            center = session.get(DocumentChunk, item["chunk_id"])
            if center is None:
                continue
            ids = [
                identity
                for identity in (center.previous_id, center.next_id, center.parent_id)
                if identity
            ]
            rows = session.execute(
                select(DocumentChunk, Document, Source)
                .join(Document, Document.id == DocumentChunk.document_id)
                .join(Source, Source.id == Document.source_id)
                .where(
                    DocumentChunk.id.in_(ids),
                    DocumentChunk.document_id == center.document_id,
                    DocumentChunk.chunker_version == center.chunker_version,
                    Document.state == "ready",
                    Source.state != "deleted",
                    _source_version_predicate(versions),
                )
                .order_by(DocumentChunk.ordinal)
                .limit(3)
            )
            for chunk, document, source in rows:
                expanded.append(
                    {
                        **_chunk_view(chunk, document, source),
                        "expanded_from": center.id,
                        "expansion": (
                            "parent" if chunk.id == center.parent_id else "neighbor"
                        ),
                    }
                )
    unique: dict[str, dict[str, Any]] = {}
    for item in [*chosen, *expanded]:
        unique.setdefault(item["chunk_id"], item)
    output: list[dict[str, Any]] = []
    chars_left, tokens_left = budget, token_budget
    multiplier = 2 if compress else 1
    per_passage = max(1, budget // (max(1, len(unique)) * multiplier))
    # UTF-8 bytes are a conservative token upper bound for both English and Hindi.
    for item in unique.values():
        excerpt = item["excerpt"]
        bound = min(per_passage, chars_left // multiplier)
        excerpt = excerpt[:bound]
        while excerpt and len(excerpt.encode("utf-8")) * multiplier > tokens_left:
            excerpt = excerpt[
                : max(
                    0,
                    len(excerpt)
                    - max(
                        1,
                        (len(excerpt.encode("utf-8")) - tokens_left // multiplier) // 3,
                    ),
                )
            ]
        if not excerpt:
            break
        view = {
            **item,
            "excerpt": excerpt,
            "truncated": bool(item.get("truncated"))
            or len(excerpt) < len(item["excerpt"]),
            "rank": len(output) + 1,
        }
        if compress:
            view["compressed_excerpt"] = compress_excerpt(excerpt, query)
            view["compression_source_chunk_id"] = item["chunk_id"]
        output.append(view)
        compressed = view.get("compressed_excerpt", "")
        chars_left -= len(excerpt) + len(compressed)
        tokens_left -= len(excerpt.encode("utf-8")) + len(compressed.encode("utf-8"))
    return output, {
        "stage": "context",
        "status": (
            "partial"
            if len(output) < len(unique) or any(p["truncated"] for p in output)
            else "ready"
        ),
        "expanded": expand,
        "compression": "extractive-v1" if compress else "off",
        "documents": len({p["document_id"] for p in output}),
        "passages": len(output),
        "character_count": budget - chars_left,
        "token_upper_bound": token_budget - tokens_left,
        "token_budget": token_budget,
    }


def advanced_search(
    session: Session,
    query: str,
    source_versions: dict[str, int],
    settings: Settings,
    *,
    mode: RetrievalMode = "hybrid",
    limit: int = 5,
    context_budget: int = 6000,
    profile: str = "advanced",
    previous_query: str | None = None,
    variants: list[str] | None = None,
    subquestions: list[str] | None = None,
    rerank: bool = False,
    expand: bool = True,
    compress: bool = False,
    consensus: bool = False,
    multi_query: bool = True,
    diversity: bool = True,
    token_budget: int = 12000,
) -> dict[str, Any]:
    if (
        not query.strip()
        or len(query) > 2000
        or not 1 <= limit <= 10
        or context_budget < 1
        or token_budget < 1
    ):
        raise RetrievalError(
            "invalid_search_bounds",
            "Search query and budgets must be bounded and nonempty.",
        )
    if profile not in {"basic", "advanced"}:
        raise RetrievalError("invalid_profile", "Retrieval profile is unavailable.")
    if profile == "basic":
        result = search(
            session, query, source_versions, settings, mode, limit, context_budget
        )
        result["profile"] = "basic"
        return result
    queries, trace = query_variants(
        query,
        previous_query=previous_query,
        variants=variants,
        aliases=settings.retrieval_aliases,
    )
    if not multi_query and not subquestions:
        queries = [query]
        trace.append({"stage": "expansion_override", "status": "skipped"})
    # Independent questions are separately labelled; not claimed as dependent hops.
    if subquestions:
        queries = list(dict.fromkeys([query, *subquestions]))[:MAX_VARIANTS]
        trace.append(
            {
                "stage": "subquestions",
                "status": "ready",
                "kind": "independent",
                "queries": queries,
            }
        )
    started = time.monotonic()
    results = []
    failures = []
    cap = MAX_CANDIDATES // len(queries)
    for index, variant in enumerate(queries):
        try:
            result = search(
                session,
                variant,
                source_versions,
                settings,
                mode,
                min(20, cap),
                min(40000, settings.max_context_characters),
                lexical_any=index > 0,
                candidate_budget=cap,
            )
        except RetrievalError as exc:
            failures.append({"variant": index, "code": exc.code})
            continue
        result["variant_index"] = index
        results.append(result)
        trace.append(
            {
                "stage": "retrieval",
                "variant": index,
                "query": variant,
                "lexical_any": index > 0,
                "stages": result["trace"],
                "candidates": len(result["passages"]),
            }
        )
    ranked = fuse_variants(results, consensus)
    trace.append(
        {
            "stage": "multi_query",
            "status": (
                "partial" if failures else "ready" if len(queries) > 1 else "skipped"
            ),
            "failures": failures,
            "candidate_budget": MAX_CANDIDATES,
            "candidate_count": sum(
                stage.get("candidate_count", 0)
                for result in results
                for stage in result["trace"]
                if stage.get("stage") in {"lexical", "dense"}
            ),
            "returned_passages": sum(len(r["passages"]) for r in results),
            "unique_count": len(ranked),
            "consensus": consensus,
            "model_calls": 0,
        }
    )
    rerank_status: dict[str, Any] = {"stage": "rerank", "status": "skipped"}
    if rerank:
        from app.retrieval.reranker import get_reranker_adapter, RerankerUnavailable

        rerank_started = time.monotonic()
        try:
            adapter = get_reranker_adapter(settings)
            bounded = ranked[:20]
            scores = adapter.score(query, [item["excerpt"] for item in bounded])
            for item, score in zip(bounded, scores, strict=True):
                item["reranker_score"] = score
            ranked = sorted(
                bounded, key=lambda item: (-item["reranker_score"], item["fusion_rank"])
            )
            rerank_status = {
                "stage": "rerank",
                "status": "ready",
                "model_id": adapter.model_id,
                "revision": adapter.revision,
                "candidate_count": len(bounded),
                "ranks": [
                    {
                        "chunk_id": item["chunk_id"],
                        "before": item["fusion_rank"],
                        "after": rank,
                        "score": item["reranker_score"],
                    }
                    for rank, item in enumerate(ranked, 1)
                ],
            }
        except RerankerUnavailable as exc:
            rerank_status = {
                "stage": "rerank",
                "status": "unavailable",
                "code": exc.code,
            }
        rerank_status["latency_ms"] = round(
            (time.monotonic() - rerank_started) * 1000, 2
        )
    trace.append(rerank_status)
    passages, context_trace = assemble_context(
        session,
        ranked,
        source_versions,
        limit=limit,
        budget=min(context_budget, settings.max_context_characters),
        token_budget=token_budget,
        expand=expand,
        compress=compress,
        query=query,
        diversity=diversity,
    )
    trace.append(context_trace)
    trace.append(
        {
            "stage": "pipeline",
            "version": PIPELINE_VERSION,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "model_calls": 0,
        }
    )
    return {
        "mode": results[0]["mode"] if results else mode,
        "requested_mode": mode,
        "profile": profile,
        "degraded": bool(failures)
        or any(r.get("degraded") for r in results)
        or rerank_status["status"] == "unavailable"
        or context_trace["status"] == "partial",
        "trace": trace,
        "passages": passages,
    }
