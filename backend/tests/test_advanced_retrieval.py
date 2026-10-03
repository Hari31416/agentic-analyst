from typing import Any

import pytest

from app.config import Settings
from app.retrieval.advanced import (
    query_variants,
    fuse_variants,
    assemble_context,
    advanced_search,
    compress_excerpt,
)
from app.retrieval.service import RetrievalError
from app.tools.documents import SearchInput


def passage(
    chunk: str, document: str = "doc", text: str = "Original income evidence."
) -> dict[str, Any]:
    return {
        "chunk_id": chunk,
        "document_id": document,
        "source_id": document,
        "source_version": 1,
        "excerpt": text,
        "location": {"page": 1},
    }


def test_queries_preserve_original_and_reject_changed_identifiers():
    queries, trace = query_variants(
        "What is S1 income 200000?",
        variants=["S2 income 300000", "S1 income 200000?"],
        aliases={"income": ["आय"]},
    )
    assert queries[0] == "What is S1 income 200000?"
    assert "S2 income 300000" not in queries
    assert trace[-1]["status"] == "fallback"
    assert len(queries) <= 3
    rewritten, trace = query_variants(
        "Does that include pension?", previous_query="S1 income 200000"
    )
    assert rewritten[0] == "Does that include pension?"
    assert rewritten[1] == "Does that include pension? S1 income 200000"
    assert trace[0]["status"] == "ready"


def test_variant_fusion_keeps_provenance_and_distinct_versions():
    results = [
        {"passages": [passage("a"), passage("b")]},
        {"passages": [passage("b"), passage("c", "version2")]},
    ]
    fused = fuse_variants(results, consensus=True)
    assert fused[0]["chunk_id"] == "b"
    assert len(fused[0]["variants"]) == 2
    assert len(fused) == 3
    assert fused[-1]["source_id"] == "version2"


def test_context_document_coverage_token_bounds_and_original_compression():
    candidates = [
        passage("a", text="Income is 200000. Apply at office."),
        passage("b"),
        passage("c", "other", "आय सीमा 200000 है।"),
    ]
    output, trace = assemble_context(
        None,
        candidates,
        {},
        limit=2,
        budget=1000,
        token_budget=1000,
        expand=False,
        compress=True,
        query="Income",
    )
    assert [p["chunk_id"] for p in output] == ["a", "c"]
    assert output[0]["excerpt"] == candidates[0]["excerpt"]
    assert output[0]["compressed_excerpt"] == "Income is 200000."
    output, trace = assemble_context(
        None,
        candidates,
        {},
        limit=3,
        budget=50,
        token_budget=30,
        expand=False,
        compress=False,
        query="income",
    )
    assert sum(len(p["excerpt"].encode()) for p in output) <= 30
    assert sum(len(p["excerpt"]) for p in output) <= 50
    assert trace["status"] == "partial"


def test_pipeline_budget_independent_questions_and_partial_failure(monkeypatch):
    import app.retrieval.advanced as module

    calls = []

    def retrieve(session, query, versions, settings, mode, limit, budget, **kwargs):
        calls.append((query, limit))
        if query == "missing":
            raise RetrievalError("embedding_unavailable", "unavailable")
        return {
            "mode": "text",
            "trace": [],
            "passages": [passage(query)],
            "degraded": False,
        }

    monkeypatch.setattr(module, "search", retrieve)
    result = advanced_search(
        None,
        "first",
        {},
        Settings(_env_file=None),
        subquestions=["second", "missing"],
        expand=False,
    )
    assert sum(limit for _, limit in calls) <= 60
    assert result["degraded"] and result["passages"]
    assert any(t.get("kind") == "independent" for t in result["trace"])
    assert all(t.get("model_calls", 0) == 0 for t in result["trace"])


def test_unavailable_requested_reranker_is_explicit(monkeypatch):
    import app.retrieval.advanced as module

    monkeypatch.setattr(
        module,
        "search",
        lambda *a, **kw: {
            "mode": "text",
            "trace": [],
            "passages": [passage("a")],
            "degraded": False,
        },
    )
    result = advanced_search(
        None, "income", {}, Settings(_env_file=None), expand=False, rerank=True
    )
    assert result["degraded"]
    assert (
        next(t for t in result["trace"] if t["stage"] == "rerank")["status"]
        == "unavailable"
    )


def test_search_input_rejects_unbounded_variants():
    with pytest.raises(ValueError):
        SearchInput(query="income", variants=["x" * 2001])
    with pytest.raises(ValueError):
        SearchInput(query="income", hop_terms=["x" * 101])
    assert compress_excerpt("Original sentence.", "unknown") == "Original sentence."


def test_literal_keyword_subsets_execute_multiple_queries_and_keep_numbers():
    queries, trace = query_variants(
        "scheme S1 active annual income maximum eligibility inclusive threshold"
    )
    assert len(queries) == 3
    assert all("S1" in query for query in queries)
    assert all(
        set(query.split()).issubset(set(queries[0].split())) for query in queries[1:]
    )


def test_compressed_context_counts_original_and_compressed_text_against_budget():
    output, trace = assemble_context(
        None,
        [passage("a", text="आय सीमा 200000 है।")],
        {},
        limit=1,
        budget=50,
        token_budget=40,
        expand=False,
        compress=True,
        query="आय",
    )
    size = sum(
        len(p["excerpt"].encode()) + len(p["compressed_excerpt"].encode())
        for p in output
    )
    assert size <= 40 and trace["token_upper_bound"] == size
