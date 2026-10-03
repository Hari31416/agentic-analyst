"""Document retrieval tools with durable, version-pinned calculation-compatible evidence."""

import asyncio
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.contracts import Contract, SafeError, ToolResult
from app.db.models import Document, Evidence, Source, Message, Run
from app.tools.structured import StructuredTools


class SearchInput(Contract):
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["lexical", "dense", "hybrid"] = "hybrid"
    limit: int = Field(default=5, ge=1, le=10)
    context_budget: int = Field(default=6000, ge=500, le=12000)

    variants: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        default_factory=list, max_length=2
    )
    subquestions: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        default_factory=list, max_length=2
    )
    multi_query: bool = True
    rerank: bool = False
    expand_context: bool | None = None
    compress: bool = False
    consensus: bool = False
    token_budget: int = Field(default=12000, ge=500, le=24000)
    hop_evidence_ids: list[UUID] = Field(default_factory=list, max_length=3)
    hop_terms: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=3
    )


class PassageInput(Contract):
    chunk_id: UUID
    neighbors: int = Field(default=1, ge=0, le=2)
    context_budget: int = Field(default=6000, ge=500, le=12000)


class DocumentTools:
    def __init__(self, runtime: Any):
        self.runtime = runtime

    async def execute(self, name: str, args: BaseModel, tool_id: str) -> ToolResult:
        from app.retrieval.service import (
            RetrievalError,
            RetrievalMode,
            get_passage,
        )

        run, sources, _, _ = StructuredTools(self.runtime).selection()
        versions = {
            source.id: run.config["source_versions"][source.id]
            for source in sources
            if source.kind in {"pdf", "docx", "txt", "md", "html", "pptx"}
        }
        if not versions:
            return ToolResult(
                status="failed",
                summary="Select at least one document source",
                error=SafeError(
                    code="no_documents_selected",
                    message="No document inputs are selected",
                ),
            )

        def retrieve() -> dict[str, Any]:
            # Ownership locks are released before local model work and read-only retrieval.
            with self.runtime.db() as session:
                self.runtime.guard(session)
            with self.runtime.db() as session:
                if name == "search_documents":
                    assert isinstance(args, SearchInput)
                    mode: RetrievalMode = "hybrid"
                    if args.mode == "lexical":
                        mode = "text"
                    elif args.mode == "dense":
                        mode = "vector"
                    from app.retrieval.advanced import advanced_search

                    profile = run.config.get("retrieval_profile", "basic")
                    if (
                        args.variants
                        or args.subquestions
                        or args.rerank
                        or args.compress
                        or args.consensus
                        or args.expand_context
                    ):
                        profile = "advanced"
                    previous = session.scalar(
                        select(Message)
                        .where(
                            Message.thread_id == run.thread_id,
                            Message.role == "user",
                            Message.run_id != run.id,
                            Message.created_at <= run.created_at,
                        )
                        .order_by(Message.created_at.desc())
                        .limit(1)
                    )
                    hop_trace = []
                    query = args.query
                    if args.hop_evidence_ids or args.hop_terms:
                        if not args.hop_evidence_ids or not args.hop_terms:
                            raise RetrievalError(
                                "invalid_hop",
                                "Dependent hops require earlier evidence and exact discovered terms.",
                            )
                        excerpts = []
                        for evidence_id in args.hop_evidence_ids:
                            evidence = session.get(Evidence, str(evidence_id))
                            evidence_run = (
                                session.get(Run, evidence.run_id) if evidence else None
                            )
                            if (
                                evidence is None
                                or evidence.kind != "document"
                                or evidence_run is None
                                or evidence_run.thread_id != run.thread_id
                                or any(
                                    versions.get(source_id) != version
                                    for source_id, version in evidence.details.get(
                                        "source_versions", {}
                                    ).items()
                                )
                                or not evidence.details.get("source_versions")
                            ):
                                raise RetrievalError(
                                    "invalid_hop_evidence",
                                    "Hop evidence must belong to this thread and selected document versions.",
                                )
                            excerpts.append(str(evidence.details.get("excerpt", "")))
                        if any(
                            not term.strip()
                            or len(term) > 100
                            or not any(term in excerpt for excerpt in excerpts)
                            for term in args.hop_terms
                        ):
                            raise RetrievalError(
                                "invalid_hop_terms",
                                "Hop terms must appear verbatim in earlier evidence.",
                            )
                        query += " " + " ".join(args.hop_terms)
                        if len(query) > 2000:
                            raise RetrievalError(
                                "query_too_long", "The hop query exceeds its bound."
                            )
                        hop_trace = [
                            {
                                "stage": "dependent_hop",
                                "status": "ready",
                                "supporting_evidence_ids": [
                                    str(i) for i in args.hop_evidence_ids
                                ],
                                "discovered_terms": args.hop_terms,
                                "original_query": args.query,
                                "query": query,
                            }
                        ]
                    result = advanced_search(
                        session,
                        query,
                        versions,
                        self.runtime.settings.model_copy(
                            update={
                                "retrieval_aliases": run.config.get(
                                    "retrieval_settings", {}
                                ).get(
                                    "aliases", self.runtime.settings.retrieval_aliases
                                )
                            }
                        ),
                        mode=mode,
                        limit=args.limit,
                        context_budget=args.context_budget,
                        profile=profile,
                        previous_query=previous.content if previous else None,
                        variants=args.variants,
                        subquestions=args.subquestions,
                        rerank=args.rerank,
                        expand=(
                            args.expand_context
                            if args.expand_context is not None
                            else profile == "advanced"
                        ),
                        compress=args.compress,
                        consensus=args.consensus,
                        multi_query=args.multi_query,
                        token_budget=args.token_budget,
                    )
                    result["trace"] = hop_trace + result["trace"]
                    return result
                assert isinstance(args, PassageInput)
                passage = get_passage(
                    session,
                    str(args.chunk_id),
                    versions,
                    self.runtime.settings,
                    neighbors=args.neighbors,
                    context_budget=args.context_budget,
                )
                return {
                    "mode": "passage",
                    "passages": [passage],
                    "trace": {"stage": "selected_passage", "neighbors": args.neighbors},
                }

        try:
            retrieved = await asyncio.to_thread(retrieve)
        except RetrievalError as exc:
            return ToolResult(
                status="failed",
                summary="Document retrieval could not complete",
                error=SafeError(code=exc.code, message=exc.message),
            )
        aliases = {"text": "lexical", "vector": "dense"}
        for key in ("mode", "requested_mode"):
            if key in retrieved:
                retrieved[key] = aliases.get(retrieved[key], retrieved[key])
        passages = retrieved.get("passages", [])
        evidence_ids = []
        with self.runtime.db() as session, session.begin():
            self.runtime.guard(session)
            for passage in passages:
                if (
                    passage["source_id"] not in versions
                    or passage["source_version"] != versions[passage["source_id"]]
                ):
                    raise ValueError("retrieval crossed the selected document versions")
                evidence_id = str(uuid4())
                evidence_ids.append(UUID(evidence_id))
                session.add(
                    Evidence(
                        id=evidence_id,
                        run_id=run.id,
                        kind="document",
                        source_ids=[passage["source_id"]],
                        details={
                            **passage,
                            "source_versions": {
                                passage["source_id"]: passage["source_version"]
                            },
                            "retrieval_mode": retrieved.get("mode"),
                            "trace": retrieved.get("trace", {}),
                            "tool_call_id": tool_id,
                        },
                    )
                )
                passage["evidence_id"] = evidence_id
        return ToolResult(
            status="partial" if retrieved.get("degraded") else "ok",
            summary=(
                f"Retrieved {len(passages)} document passages using {retrieved.get('mode', 'unavailable')}."
                if passages
                else "No supporting passages were found; reformulate or ask for missing evidence."
            ),
            evidence_ids=evidence_ids,
            data=retrieved,
        )
