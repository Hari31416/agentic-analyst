"""Document retrieval tools with durable, version-pinned calculation-compatible evidence."""

import asyncio
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.contracts import Contract, SafeError, ToolResult
from app.db.models import Document, Evidence, Source
from app.tools.structured import StructuredTools


class SearchInput(Contract):
    query: str = Field(min_length=1, max_length=2000)
    mode: Literal["lexical", "dense", "hybrid"] = "hybrid"
    limit: int = Field(default=5, ge=1, le=10)
    context_budget: int = Field(default=6000, ge=500, le=12000)


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
            search,
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
                    return search(
                        session,
                        args.query,
                        versions,
                        self.runtime.settings,
                        mode=mode,
                        limit=args.limit,
                        context_budget=args.context_budget,
                    )
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
