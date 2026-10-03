"""Agent tool for cached document, section, and corpus overview summaries."""

from __future__ import annotations

import asyncio
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from app.contracts import Contract, SafeError, ToolResult
from app.db.models import Evidence
from app.retrieval.summaries import SummaryError, summarize
from app.tools.structured import StructuredTools


class SummaryInput(Contract):
    scope: Literal["document", "section", "overview"] = "overview"
    source_id: UUID | None = None
    section: str | None = Field(default=None, min_length=1, max_length=300)
    thematic: bool = False


class SummaryTools:
    """Create summaries whose original supporting excerpts remain inspectable."""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    async def execute(self, name: str, args: BaseModel, tool_id: str) -> ToolResult:
        if name != "summarize_documents" or not isinstance(args, SummaryInput):
            return ToolResult(
                status="failed",
                summary="Unknown summary operation",
                error=SafeError(
                    code="unknown_summary_operation",
                    message="Choose summarize_documents.",
                ),
            )
        run, sources, _, _ = StructuredTools(self.runtime).selection()
        document_sources = {
            source.id: source
            for source in sources
            if source.kind in {"pdf", "docx", "txt", "md", "html", "pptx"}
        }
        versions = {
            source_id: version
            for source_id, version in run.config.get("source_versions", {}).items()
            if source_id in document_sources
        }
        source_id = str(args.source_id) if args.source_id else None

        def build() -> dict[str, Any]:
            # Validate lease and cancellation, then release the run row lock before
            # scoring the bounded chunks. Cache work is global and idempotent.
            with self.runtime.db() as session, session.begin():
                self.runtime.guard(session)
            with self.runtime.db() as session, session.begin():
                result = summarize(
                    session,
                    versions,
                    scope=args.scope,
                    source_id=source_id,
                    section=args.section,
                    thematic=args.thematic,
                )
                return result

        try:
            summary = await asyncio.to_thread(build)
        except SummaryError as exc:
            return ToolResult(
                status="failed",
                summary="Document summary could not be produced",
                error=SafeError(code=exc.code, message=exc.message),
            )

        # Summary prose itself is never stored as evidence. The evidence IDs
        # returned here identify the original, version-pinned chunks only.
        evidence_ids: list[UUID] = []
        support = summary.get("supporting_passages", [])
        with self.runtime.db() as session, session.begin():
            self.runtime.guard(session)
            for passage in support:
                identity = passage["source_id"]
                if versions.get(identity) != passage["source_version"]:
                    raise ValueError("summary crossed the selected source version")
                evidence_id = str(uuid4())
                evidence_ids.append(UUID(evidence_id))
                session.add(
                    Evidence(
                        id=evidence_id,
                        run_id=run.id,
                        kind="document",
                        source_ids=[identity],
                        details={
                            "source_id": identity,
                            "source_version": passage["source_version"],
                            "document_version": passage["document_version"],
                            "document_id": passage["document_id"],
                            "document_name": passage["source_name"],
                            "chunk_id": passage["chunk_id"],
                            "location": passage["location"],
                            "heading": passage["heading"],
                            "excerpt": passage["excerpt"],
                            "summary_fingerprint": summary["cache"]["fingerprint"],
                            "tool_call_id": tool_id,
                            "source_versions": {identity: passage["source_version"]},
                        },
                    )
                )
                passage["evidence_id"] = evidence_id

        partial = bool(summary.get("truncated"))
        return ToolResult(
            status="partial" if partial else "ok",
            summary=(
                "Created a bounded extractive summary; inspect its supporting passages."
                if not partial
                else "Created a partial extractive summary from a bounded sample; inspect its supporting passages and coverage."
            ),
            evidence_ids=evidence_ids,
            data=summary,
        )
