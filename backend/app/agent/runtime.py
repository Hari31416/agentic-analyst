import asyncio
import hashlib
import json
from contextlib import suppress
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.loop import AgentLoop, BudgetExhausted, PROMPT_VERSION, Tool
from app.agent.model import ModelError, OpenAICompatibleModel
from app.agent.protocol import ModelToolCall
from app.config import Settings
from app.contracts import (
    Contract,
    FinalAnswer,
    SafeError,
    ToolResult,
    RunState,
    TERMINAL_STATES,
)
from app.db.models import Artifact, Job, Message, Run, Source, Thread, ToolCall, now
from app.db.repository import append_event, audit
from app.evidence.validation import validate_answer
from app.sandbox.client import SandboxError, SandboxHTTPClient
from app.storage.factory import get_storage
from app.storage.s3 import StorageUnavailable
from app.tools.python import PythonExecution
from app.tools.errors import ToolInputError
from app.sources.connections import ConnectorError
from app.policy.sql import SqlPolicyError
from app.workers.queue import Claim, LeaseLost, owned


class PythonInput(Contract):
    code: str = Field(min_length=1, max_length=64000)
    output_paths: list[str] | None = Field(default=None, max_length=16)
    input_dataset_ids: list[UUID] = Field(default_factory=list, max_length=16)
    input_artifact_ids: list[UUID] = Field(default_factory=list, max_length=16)
    timeout_seconds: int = Field(default=120, ge=1, le=300)


class InspectInput(Contract):
    artifact_id: UUID
    max_characters: int = Field(default=4000, ge=1, le=8000)


class NoInput(Contract):
    pass


class RunCancelled(RuntimeError):
    pass


class RunRuntime:
    def __init__(self, task: Claim, settings: Settings, db: sessionmaker[Session]):
        self.task = task
        self.settings = settings
        self.db = db
        self.python: PythonExecution | None = None
        self.sandbox: SandboxHTTPClient | None = None
        self.active_tool_call_id: str | None = None

    def guard(self, session: Session, allow_cancelled: bool = False) -> Run:
        # Lock ownership before every durable action, including events/artifact metadata.
        job = session.scalar(
            select(Job).where(owned(self.task.id, self.task.token)).with_for_update()
        )
        if job is None or not job.run_id:
            raise LeaseLost("run job ownership lost")
        run = session.scalar(select(Run).where(Run.id == job.run_id).with_for_update())
        if run is None:
            raise ValueError("run not found")
        if run.state == "cancelled" and not allow_cancelled:
            raise RunCancelled("run cancelled")
        return run

    async def event(self, kind: str, payload: dict[str, Any]) -> None:
        with self.db() as session, session.begin():
            run = self.guard(session)
            append_event(session, run.id, kind, payload)
            if kind == "tool_rejected":
                session.add(
                    ToolCall(
                        run_id=run.id,
                        provider_call_id=payload["call_id"],
                        name=payload["name"],
                        input_reference={
                            "validation_errors": payload.get("validation_errors", [])
                        },
                        decision="rejected",
                        status="rejected",
                    )
                )
                audit(
                    session,
                    run_id=run.id,
                    action="tool.validate",
                    decision="rejected",
                    reason_code=payload["code"],
                )

    async def answer_valid(self, answer: FinalAnswer) -> None:
        with self.db() as session:
            run = self.guard(session)
            validate_answer(session, run, answer)

    def accessible_artifacts(self, session: Session, run: Run) -> list[Artifact]:
        rows = session.scalars(
            select(Artifact)
            .join(Run, Artifact.run_id == Run.id)
            .where(Run.thread_id == run.thread_id)
        )
        # Never stage outputs built from sources outside this run's selection.
        permitted = []
        for row in rows:
            producer = session.get(Run, row.run_id) if row.run_id else None
            if producer is not None and set(producer.selected_source_ids).issubset(
                set(run.selected_source_ids)
            ):
                versions = producer.config.get("source_versions", {})
                current_versions = run.config.get("source_versions", {})
                if all(
                    (source := session.get(Source, identity)) is not None
                    and source.state == "ready"
                    and source.version == version
                    and current_versions.get(identity) == version
                    for identity, version in versions.items()
                ):
                    permitted.append(row)
        return permitted

    async def dispatch(
        self, name: str, call: ModelToolCall, arguments: BaseModel
    ) -> ToolResult:
        with self.db() as session, session.begin():
            run = self.guard(session)
            existing = session.scalar(
                select(ToolCall).where(
                    ToolCall.run_id == run.id, ToolCall.provider_call_id == call.id
                )
            )
            if existing:
                if existing.status == "completed" and existing.result:
                    return ToolResult.model_validate(existing.result)
                raise ValueError("an earlier tool execution has an unresolved outcome")
            tool = ToolCall(
                run_id=run.id,
                provider_call_id=call.id,
                name=name,
                input_reference={
                    "sha256": hashlib.sha256(call.arguments.encode()).hexdigest()
                },
                decision="allowed",
                status="running",
            )
            session.add(tool)
            session.flush()
            tool_id = tool.id
            thread = session.get(Thread, run.thread_id)
            assert thread is not None
            workspace_id = thread.workspace_id
            run_id = run.id
            selected_sources = run.selected_source_ids
            append_event(
                session, run.id, "tool_started", {"name": name, "tool_call_id": tool_id}
            )
            audit(
                session,
                run_id=run.id,
                tool_call_id=tool_id,
                action="tool.dispatch",
                decision="allowed",
                reason_code="validated_input",
            )
            self.active_tool_call_id = tool_id
        try:
            if name == "run_python":
                assert isinstance(arguments, PythonInput)
                result = await self.run_python(workspace_id, run_id, tool_id, arguments)
            elif name == "analyze_data":
                from app.tools.analysis import AnalysisTools, AnalyzeInput

                assert isinstance(arguments, AnalyzeInput)
                result = await AnalysisTools(self).execute(arguments, tool_id)
            elif name == "generate_report":
                from app.tools.reports import ReportsTool, GenerateReportInput

                assert isinstance(arguments, GenerateReportInput)
                result = await ReportsTool(self).execute(name, arguments, tool_id)
            elif name == "summarize_documents":
                from app.tools.summaries import SummaryTools

                result = await SummaryTools(self).execute(name, arguments, tool_id)
            elif name in {"search_documents", "source_passage"}:
                from app.tools.documents import DocumentTools

                result = await DocumentTools(self).execute(name, arguments, tool_id)
            elif name in {
                "list_sources",
                "dataset_profile",
                "inspect_schema",
                "sample_rows",
                "run_sql",
                "register_dataset",
            }:
                from app.tools.structured import StructuredTools

                result = await StructuredTools(self).execute(name, arguments, tool_id)
            elif name == "list_artifacts":
                with self.db() as session:
                    run = self.guard(session)
                    rows = self.accessible_artifacts(session, run)[-100:]
                    result = ToolResult(
                        status="ok",
                        summary=f"{len(rows)} retained artifacts available",
                        artifact_ids=[UUID(row.id) for row in rows],
                        data={
                            "artifacts": [
                                {
                                    "id": row.id,
                                    "name": row.display_name,
                                    "media_type": row.media_type,
                                    "bytes": row.byte_size,
                                }
                                for row in rows
                            ]
                        },
                    )
            else:
                assert isinstance(arguments, InspectInput)
                with self.db() as session:
                    run = self.guard(session)
                    artifact = next(
                        (
                            a
                            for a in self.accessible_artifacts(session, run)
                            if a.id == str(arguments.artifact_id)
                        ),
                        None,
                    )
                    if artifact is None:
                        raise ValueError("artifact is outside this run's inputs")
                    if not (
                        artifact.media_type.startswith("text/")
                        or artifact.media_type == "application/json"
                    ):
                        result = ToolResult(
                            status="ok",
                            summary="Binary artifact; use its download link",
                            artifact_ids=[arguments.artifact_id],
                            data={
                                "media_type": artifact.media_type,
                                "byte_size": artifact.byte_size,
                            },
                        )
                    else:
                        content = await asyncio.to_thread(
                            get_storage(self.settings).read,
                            artifact.storage_key,
                            self.settings.max_upload_bytes,
                        )
                        excerpt = content.decode("utf-8", errors="replace")[
                            : arguments.max_characters
                        ]
                        result = ToolResult(
                            status=(
                                "partial"
                                if len(content) > len(excerpt.encode())
                                else "ok"
                            ),
                            summary=excerpt,
                            artifact_ids=[arguments.artifact_id],
                            data={
                                "truncated": len(content) > len(excerpt.encode()),
                                "byte_size": artifact.byte_size,
                            },
                        )
        except (
            ValueError,
            OSError,
            SandboxError,
            StorageUnavailable,
            ConnectorError,
        ) as exc:
            code = (
                exc.code
                if isinstance(
                    exc, (SandboxError, ConnectorError, SqlPolicyError, ToolInputError)
                )
                else "tool_execution_failed"
            )
            result = ToolResult(
                status="failed",
                summary="The tool could not complete safely",
                error=SafeError(
                    code=code,
                    message=(
                        str(exc)[:300]
                        if isinstance(
                            exc, (SqlPolicyError, ConnectorError, ToolInputError)
                        )
                        else "Check selected inputs and service availability"
                    ),
                ),
            )
        with self.db() as session, session.begin():
            run = self.guard(session)
            completed_tool = session.get(ToolCall, tool_id)
            assert completed_tool is not None
            for item in result.data.get("artifacts", []):
                if not isinstance(item, dict) or "storage_key" not in item:
                    continue
                artifact = session.get(Artifact, item["id"])
                if artifact is None:
                    session.add(
                        Artifact(
                            id=item["id"],
                            run_id=run_id,
                            tool_call_id=tool_id,
                            storage_key=item["storage_key"],
                            display_name=item["display_name"],
                            media_type=item["media_type"],
                            byte_size=item["byte_size"],
                            sha256=item["sha256"],
                            lineage=[
                                *selected_sources,
                                *[
                                    f"source:{identity}@{version}"
                                    for identity, version in run.config.get(
                                        "source_versions", {}
                                    ).items()
                                ],
                                *[
                                    f"dataset:{identity}"
                                    for identity in result.data.get(
                                        "input_dataset_ids", []
                                    )
                                ],
                                *[
                                    f"artifact:{identity}"
                                    for identity in result.data.get(
                                        "input_artifact_ids", []
                                    )
                                ],
                                *result.data.get("input_lineage", []),
                                *[
                                    f"artifact:{snapshot['id']}@sha256:{snapshot['sha256']}"
                                    for snapshot in result.data.get(
                                        "staged_input_artifacts", []
                                    )
                                ],
                                *item.get("lineage", []),
                                *result.data.get("artifact_lineage", {}).get(
                                    item["id"], []
                                ),
                                f"tool:{tool_id}",
                            ],
                            durable=True,
                        )
                    )
            completed_tool.status = "completed"
            completed_tool.result = result.model_dump(mode="json")
            completed_tool.finished_at = now()
            append_event(
                session,
                run.id,
                "tool_finished",
                {
                    "name": name,
                    "tool_call_id": tool_id,
                    "status": result.status,
                    "summary": result.summary[:500],
                    "artifact_ids": [str(i) for i in result.artifact_ids],
                },
            )
            audit(
                session,
                run_id=run.id,
                tool_call_id=tool_id,
                action="tool.result",
                decision=result.status,
                reason_code=result.error.code if result.error else "completed",
            )
            self.active_tool_call_id = None
        return result

    async def run_python(
        self, workspace_id: str, run_id: str, tool_id: str, arguments: PythonInput
    ) -> ToolResult:
        settings = self.settings
        if not settings.sandbox_base_url or not settings.sandbox_image:
            raise SandboxError(
                "Sandbox is not configured", code="sandbox_not_configured"
            )
        storage = get_storage(settings)
        # Retain the exact code before execution, then link every output to its tool call.
        stored = await asyncio.to_thread(
            storage.put,
            f"derived/{workspace_id}/{run_id}/{tool_id}/code.py",
            arguments.code.encode(),
        )
        with self.db() as session, session.begin():
            run = self.guard(session)
            code = Artifact(
                run_id=run_id,
                tool_call_id=tool_id,
                storage_key=stored.key,
                display_name="code.py",
                media_type="text/x-python",
                byte_size=stored.byte_size,
                sha256=stored.sha256,
                lineage=[
                    *run.selected_source_ids,
                    *[
                        f"source:{identity}@{version}"
                        for identity, version in run.config.get(
                            "source_versions", {}
                        ).items()
                    ],
                ],
            )
            session.add(code)
            session.flush()
            code_id = code.id
            tool = session.get(ToolCall, tool_id)
            assert tool is not None
            tool.input_reference = {"code_artifact_id": code_id}
        if self.python is None:
            self.sandbox = SandboxHTTPClient(
                settings.sandbox_base_url,
                image=settings.sandbox_image,
                auth_token=(
                    settings.sandbox_auth_token.get_secret_value()
                    if settings.sandbox_auth_token
                    else None
                ),
                session_timeout_seconds=settings.run_timeout_seconds + 300,
            )
            self.python = PythonExecution(
                self.sandbox,
                storage,
                workspace_id,
                run_id,
                max_output_bytes=min(settings.max_upload_bytes, 8 * 1024 * 1024),
                max_console_bytes=8000,
            )
        # Persist the session before the first guest operation can be interrupted.
        sandbox_session = await self.python._ensure_session()
        with self.db() as session, session.begin():
            run = self.guard(session, allow_cancelled=True)
            run.config = {
                **run.config,
                "sandbox_session_id": sandbox_session.id,
            }
        # Cancellation can arrive while the sandbox service is creating the
        # session. Persist its ID for cleanup, then reject guest work if cancelled.
        with self.db() as session:
            self.guard(session)
        # Staged file inputs are canonical working copies of selected versioned datasets.
        staged_datasets: list[str] = []
        staged_inputs: list[dict[str, Any]] = []
        input_lineage: list[str] = []
        if arguments.input_dataset_ids:
            from app.tools.structured import StructuredTools
            from app.sources.files import working_csv

            _, sources, datasets, _ = StructuredTools(self).selection()
            for dataset_id in dict.fromkeys(arguments.input_dataset_ids):
                dataset = next(
                    (row for row in datasets if row.id == str(dataset_id)), None
                )
                if dataset is None:
                    raise ToolInputError(
                        "input dataset is not selected; use an exact dataset_id from list_sources"
                    )
                source = next(row for row in sources if row.id == dataset.source_id)
                if source.kind not in {"csv", "xlsx", "xls", "json", "parquet"}:
                    raise ToolInputError(
                        "database credentials cannot enter the guest; use run_sql first and stage the returned CSV artifact_id instead"
                    )
                content = await asyncio.to_thread(
                    working_csv, source, dataset, storage, settings.max_upload_bytes
                )
                with self.db() as session:
                    self.guard(session)
                assert self.sandbox is not None
                await self.sandbox.write(
                    sandbox_session.id, f"inputs/{dataset_id}.csv", content
                )
                staged_datasets.append(dataset.id)
                input_lineage.extend(dataset.lineage or [])
                snapshot = await asyncio.to_thread(
                    storage.put,
                    f"derived/{workspace_id}/{run_id}/{tool_id}/inputs/{dataset_id}.csv",
                    content,
                )
                with self.db() as session, session.begin():
                    live = self.guard(session)
                    retained = Artifact(
                        run_id=run_id,
                        tool_call_id=tool_id,
                        storage_key=snapshot.key,
                        display_name=f"input-{dataset_id}.csv",
                        media_type="text/csv",
                        byte_size=snapshot.byte_size,
                        sha256=snapshot.sha256,
                        lineage=[
                            *live.selected_source_ids,
                            *(dataset.lineage or []),
                            *[
                                f"source:{i}@{v}"
                                for i, v in live.config.get(
                                    "source_versions", {}
                                ).items()
                            ],
                            f"dataset:{dataset_id}",
                            f"tool:{tool_id}",
                        ],
                    )
                    session.add(retained)
                    session.flush()
                    staged_inputs.append(
                        {
                            "id": retained.id,
                            "dataset_id": str(dataset_id),
                            "guest_path": f"/workspace/inputs/{dataset_id}.csv",
                            "sha256": snapshot.sha256,
                        }
                    )
        # Resuming analysis uses durable artifacts; credentials and arbitrary paths are never staged.
        if arguments.input_artifact_ids:
            for artifact_id in arguments.input_artifact_ids:
                with self.db() as session:
                    run = self.guard(session)
                    artifact = next(
                        (
                            a
                            for a in self.accessible_artifacts(session, run)
                            if a.id == str(artifact_id)
                        ),
                        None,
                    )
                    if artifact is None:
                        raise ToolInputError(
                            "input artifact is unavailable for the selected source versions; use an exact accessible artifact_id"
                        )
                    content = await asyncio.to_thread(
                        storage.read, artifact.storage_key, settings.max_upload_bytes
                    )
                input_lineage.extend(artifact.lineage or [])
                if hashlib.sha256(content).hexdigest() != artifact.sha256:
                    raise ValueError("artifact integrity check failed")
                assert self.sandbox is not None
                with self.db() as session:
                    self.guard(session)
                await self.sandbox.write(
                    sandbox_session.id, f"inputs/{artifact_id}", content
                )
        with self.db() as session:
            self.guard(session)
        result = await self.python.execute(
            arguments.code,
            tool_id,
            arguments.output_paths,
            timeout_seconds=min(
                arguments.timeout_seconds, settings.run_timeout_seconds
            ),
        )
        result.artifact_ids.insert(0, UUID(code_id))
        result.artifact_ids.extend(UUID(i["id"]) for i in staged_inputs)
        result.data["staged_input_artifacts"] = staged_inputs
        result.data["input_lineage"] = list(dict.fromkeys(input_lineage))
        # Paths and object keys are server-owned. Expose only usable guest input locations and references.
        result.data["code_artifact_id"] = code_id
        result.data["input_dataset_ids"] = staged_datasets
        result.data["input_artifact_ids"] = [
            str(i) for i in arguments.input_artifact_ids
        ]
        with self.db() as session, session.begin():
            self.guard(session)
            retained_code = session.get(Artifact, code_id)
            assert retained_code is not None
            retained_code.lineage = [
                *retained_code.lineage,
                *[f"dataset:{identity}" for identity in staged_datasets],
                *[f"artifact:{identity}" for identity in arguments.input_artifact_ids],
                *list(dict.fromkeys(input_lineage)),
            ]
        return result

    async def run(self) -> None:
        try:
            with self.db() as session, session.begin():
                run = self.guard(session, allow_cancelled=True)
                if run.state == "cancelled":
                    return
                if run.state != "queued":
                    # Never repeat a potentially side-effecting guest command after an abandoned lease.
                    self.finalize(
                        session,
                        run,
                        "failed",
                        {
                            "partial": True,
                            "error": {
                                "code": "worker_restart",
                                "message": "Prior execution was interrupted; retained outputs remain available. Retry as a new run.",
                            },
                        },
                    )
                    return
                run.state = "running"
                run.started_at = now()
                history = [
                    {
                        "id": row.id,
                        "role": row.role,
                        "content": row.content,
                        "selected_source_ids": row.selected_source_ids,
                        "references": row.references,
                        "selected_dataset_ids": (message_config or {}).get(
                            "selected_dataset_ids", []
                        ),
                        "source_versions": (message_config or {}).get(
                            "source_versions", {}
                        ),
                    }
                    for row, message_config in session.execute(
                        select(Message, Run.config)
                        .outerjoin(Run, Run.id == Message.run_id)
                        .where(
                            Message.thread_id == run.thread_id,
                            Message.created_at <= run.created_at,
                        )
                        .order_by(Message.created_at, Message.id)
                    )
                ]
                # The current user message can have a later DB timestamp than its run.
                current = session.scalar(
                    select(Message).where(
                        Message.run_id == run.id, Message.role == "user"
                    )
                )
                assert current is not None
                if not any(message.get("id") == current.id for message in history):
                    history.append(
                        {
                            "id": current.id,
                            "role": "user",
                            "content": current.content,
                            "selected_source_ids": current.selected_source_ids,
                            "references": current.references,
                            "selected_dataset_ids": run.config.get(
                                "selected_dataset_ids", []
                            ),
                            "source_versions": run.config.get("source_versions", {}),
                        }
                    )
                language = str(run.config["answer_language"])
                append_event(
                    session,
                    run.id,
                    "status",
                    {"state": "running", "message": "Worker started"},
                )

            async def python(call: ModelToolCall, args: BaseModel) -> ToolResult:
                return await self.dispatch("run_python", call, args)

            async def inspect(call: ModelToolCall, args: BaseModel) -> ToolResult:
                return await self.dispatch("inspect_artifact", call, args)

            async def list_outputs(call: ModelToolCall, args: BaseModel) -> ToolResult:
                return await self.dispatch("list_artifacts", call, args)

            from app.tools.structured import (
                DatasetInput,
                RegisterInput,
                SampleInput,
                SourceInput,
                SQLInput,
            )

            def structured_tool(
                name: str, description: str, schema: type[BaseModel]
            ) -> Tool:
                async def execute(call: ModelToolCall, args: BaseModel) -> ToolResult:
                    return await self.dispatch(name, call, args)

                return Tool(name, description, schema, execute)

            from app.tools.documents import PassageInput, SearchInput
            from app.tools.summaries import SummaryInput
            from app.tools.analysis import AnalyzeInput
            from app.tools.reports import GenerateReportInput

            structured_tools = [
                structured_tool(
                    "analyze_data",
                    "Run bounded cleaning, joins, reshaping, exact decimal arithmetic, date/fiscal derivations and statistics in the microVM. Supply selected dataset IDs or CSV artifact snapshots. Explicitly convert numeric/date columns. Rejects missing and duplicate keys unless cleaned; join relationship defaults many_to_one. Record assumptions, units and chart labels. DB/file analysis first uses run_sql to retain a bounded CSV snapshot.",
                    AnalyzeInput,
                ),
                structured_tool(
                    "generate_report",
                    "Generate Markdown/PDF and a replay notebook from retained results and document evidence. Supply exact accessible artifact/evidence IDs; calculated numbers and citations are preserved. Inputs and code snapshots must be available for replay.",
                    GenerateReportInput,
                ),
                structured_tool(
                    "summarize_documents",
                    "Get cached document, section or multi-document overview summaries with original supporting evidence. Summaries are navigation aids; cite original passages for claims. Optional thematic headings group the bounded sample.",
                    SummaryInput,
                ),
                structured_tool(
                    "search_documents",
                    "Search selected document versions using lexical, dense or hybrid retrieval. Advanced profile fuses up to three queries with optional local rerank, context expansion, extractive compression, and independent subquestions. For dependent hops supply hop_evidence_ids and hop_terms copied verbatim from earlier evidence to locate the next passage. Returns original excerpts and stage traces. Never infer unsupported criteria.",
                    SearchInput,
                ),
                structured_tool(
                    "source_passage",
                    "Inspect an exact selected document chunk with bounded neighboring original passages, stable locations, and evidence.",
                    PassageInput,
                ),
                structured_tool(
                    "list_sources",
                    "List selected sources and datasets, with SQL table names. Start here before source analysis.",
                    NoInput,
                ),
                structured_tool(
                    "dataset_profile",
                    "Inspect a selected dataset's columns, types, units, and warnings.",
                    DatasetInput,
                ),
                structured_tool(
                    "inspect_schema",
                    "Inspect selected sheets or database tables in a selected source.",
                    SourceInput,
                ),
                structured_tool(
                    "sample_rows",
                    "Read a bounded page from a selected dataset. A sample is not the full dataset for aggregates.",
                    SampleInput,
                ),
                structured_tool(
                    "run_sql",
                    "Execute one read-only query. For file SQL use selected dataset_ids and listed data_UUID aliases; all file columns are VARCHAR, so explicitly cast numeric/date columns. For database SQL supply source_id and exact schema.table identifiers. Output limits apply after full aggregation. SQL, CSV, and evidence are retained.",
                    SQLInput,
                ),
                structured_tool(
                    "register_dataset",
                    "Register an accessible CSV output as a derived source with lineage. Select it in a later run.",
                    RegisterInput,
                ),
            ]
            loop = AgentLoop(
                OpenAICompatibleModel(self.settings),
                self.settings,
                [
                    Tool(
                        "run_python",
                        "Execute Python in the isolated microVM. Write output files relative to the current working directory; list output_paths relative to that directory. Imported durable artifacts are at /workspace/inputs/{artifact_id}; selected datasets supplied in input_dataset_ids are UTF-8 CSV at /workspace/inputs/{dataset_id}.csv. No network, secrets, or source modifications.",
                        PythonInput,
                        python,
                    ),
                    Tool(
                        "list_artifacts",
                        "List retained output artifacts available to this thread and selection.",
                        NoInput,
                        list_outputs,
                    ),
                    Tool(
                        "inspect_artifact",
                        "Read a bounded text excerpt or metadata of an existing artifact.",
                        InspectInput,
                        inspect,
                    ),
                    *structured_tools,
                ],
                self.event,
                self.answer_valid,
            )
            answer = await loop.run(history, language)
            with self.db() as session, session.begin():
                run = self.guard(session)
                validate_answer(session, run, answer)
                session.add(
                    Message(
                        thread_id=run.thread_id,
                        run_id=run.id,
                        role="assistant",
                        content=answer.text,
                        selected_source_ids=run.selected_source_ids,
                        references={
                            "evidence_ids": [str(i) for i in answer.evidence_ids],
                            "artifact_ids": [str(i) for i in answer.artifact_ids],
                        },
                    )
                )
                append_event(session, run.id, "answer", answer.model_dump(mode="json"))
                self.finalize(
                    session,
                    run,
                    "awaiting_clarification" if answer.clarification else "completed",
                    {
                        **answer.model_dump(mode="json"),
                        "usage": loop.usage,
                        "model_calls": loop.model_calls,
                        "tool_calls": loop.calls,
                        "prompt_version": PROMPT_VERSION,
                    },
                )
        except (BudgetExhausted, ModelError, ValueError) as exc:
            code = (
                exc.code
                if isinstance(exc, ModelError)
                else (
                    "budget_exhausted"
                    if isinstance(exc, BudgetExhausted)
                    else "invalid_model_response"
                )
            )
            message = (
                str(exc)
                if isinstance(exc, (BudgetExhausted, ModelError))
                else "The model returned an invalid response"
            )
            with self.db() as session, session.begin():
                run = self.guard(session, allow_cancelled=True)
                if run.state not in TERMINAL_STATES:
                    self._fail_open_tool_calls(session, run, code)
                    self.finalize(
                        session,
                        run,
                        (
                            "budget_exhausted"
                            if isinstance(exc, BudgetExhausted)
                            else "failed"
                        ),
                        {"partial": True, "error": {"code": code, "message": message}},
                    )
        except RunCancelled:
            # API cancellation may race a provider callback. The finalizer below
            # still runs while the caller retains its job lease.
            raise
        except asyncio.CancelledError:
            with self.db() as session, session.begin():
                run = self.guard(session, allow_cancelled=True)
                run.outcome = {
                    **(run.outcome or {}),
                    "partial": True,
                    "cleanup": "pending",
                }
                salvage = self.python.cancelled_result if self.python else None
                if salvage:
                    for item in salvage.data.get("artifacts", []):
                        if session.get(Artifact, item["id"]) is None:
                            session.add(
                                Artifact(
                                    id=item["id"],
                                    run_id=run.id,
                                    tool_call_id=item["tool_call_id"],
                                    storage_key=item["storage_key"],
                                    display_name=item["display_name"],
                                    media_type=item["media_type"],
                                    byte_size=item["byte_size"],
                                    sha256=item["sha256"],
                                    lineage=[
                                        *run.selected_source_ids,
                                        f"tool:{item['tool_call_id']}",
                                    ],
                                    durable=True,
                                )
                            )
                self._save_cancelled_tool(session, run, salvage)
                if run.state not in TERMINAL_STATES:
                    self.finalize(
                        session,
                        run,
                        "failed",
                        {
                            "partial": True,
                            "cleanup": "pending",
                            "error": {
                                "code": "worker_interrupted",
                                "message": "Worker interrupted the run",
                            },
                        },
                    )
            raise
        except Exception:
            # Keep provider, guest and internal exception details out of user-facing
            # run state and logs. A failed run is terminal; it is never replayed.
            with self.db() as session, session.begin():
                run = self.guard(session, allow_cancelled=True)
                self._fail_open_tool_calls(session, run, "worker_failed")
                if run.state not in TERMINAL_STATES:
                    self.finalize(
                        session,
                        run,
                        "failed",
                        {
                            "partial": True,
                            "error": {
                                "code": "worker_failed",
                                "message": "The run stopped unexpectedly. Retry as a new run.",
                            },
                        },
                    )
        finally:
            cleanup = "complete"
            session_id = None
            try:
                with self.db() as session:
                    run = self.guard(session, allow_cancelled=True)
                    session_id = run.config.get("sandbox_session_id")
            except (LeaseLost, RunCancelled):
                # A new owner may be using this run's session. Do not tear it down.
                raise
            if self.python:
                try:
                    await self.python.aclose()
                except Exception:
                    cleanup = "failed"
            elif session_id:
                # Handles cancellation/restart before PythonExecution was rebuilt.
                try:
                    if (
                        not self.settings.sandbox_base_url
                        or not self.settings.sandbox_image
                    ):
                        raise SandboxError(
                            "Sandbox is not configured", code="sandbox_not_configured"
                        )
                    self.sandbox = SandboxHTTPClient(
                        self.settings.sandbox_base_url,
                        image=self.settings.sandbox_image,
                        auth_token=(
                            self.settings.sandbox_auth_token.get_secret_value()
                            if self.settings.sandbox_auth_token
                            else None
                        ),
                        session_timeout_seconds=self.settings.run_timeout_seconds + 300,
                    )
                    await self.sandbox.stop(session_id)
                    await self.sandbox.delete_session(session_id)
                except Exception:
                    cleanup = "failed"
            if self.sandbox:
                try:
                    await self.sandbox.aclose()
                except Exception:
                    cleanup = "failed"
            with self.db() as session, session.begin():
                run = self.guard(session, allow_cancelled=True)
                self._fail_open_tool_calls(session, run, "worker_interrupted")
                if run.state not in TERMINAL_STATES:
                    self.finalize(
                        session,
                        run,
                        "failed",
                        {
                            "partial": True,
                            "error": {
                                "code": "worker_interrupted",
                                "message": "The run stopped before producing a final answer.",
                            },
                        },
                    )
                run.outcome = {**(run.outcome or {}), "cleanup": cleanup}
                append_event(
                    session,
                    run.id,
                    "terminal",
                    {"state": run.state, "cleanup": cleanup},
                )
                audit(
                    session,
                    run_id=run.id,
                    action="sandbox.cleanup",
                    decision=cleanup,
                    reason_code=cleanup,
                )

    def _save_cancelled_tool(
        self, session: Session, run: Run, salvage: ToolResult | None
    ) -> None:
        if salvage:
            for item in salvage.data.get("artifacts", []):
                if session.get(Artifact, item["id"]) is None:
                    session.add(
                        Artifact(
                            id=item["id"],
                            run_id=run.id,
                            tool_call_id=item["tool_call_id"],
                            storage_key=item["storage_key"],
                            display_name=item["display_name"],
                            media_type=item["media_type"],
                            byte_size=item["byte_size"],
                            sha256=item["sha256"],
                            lineage=[
                                *run.selected_source_ids,
                                f"tool:{item['tool_call_id']}",
                            ],
                            durable=True,
                        )
                    )
        pending = list(
            session.scalars(
                select(ToolCall).where(
                    ToolCall.run_id == run.id, ToolCall.status == "running"
                )
            )
        )
        for tool in pending:
            if salvage and tool.id == self.active_tool_call_id:
                tool.status = "completed"
                tool.result = salvage.model_dump(mode="json")
                tool.result["data"]["partial_after_cancellation"] = True
            else:
                tool.status = "failed"
                tool.result = {
                    "status": "failed",
                    "summary": "Tool execution was interrupted",
                    "error": {"code": "worker_interrupted"},
                }
            tool.finished_at = now()

    @staticmethod
    def _fail_open_tool_calls(session: Session, run: Run, code: str) -> None:
        for tool in session.scalars(
            select(ToolCall).where(
                ToolCall.run_id == run.id, ToolCall.status == "running"
            )
        ):
            tool.status = "failed"
            tool.result = {
                "status": "failed",
                "summary": "Tool execution did not complete",
                "error": {"code": code},
            }
            tool.finished_at = now()

    @staticmethod
    def finalize(
        session: Session, run: Run, state: str, outcome: dict[str, Any]
    ) -> None:
        run.state = state
        run.outcome = {**outcome, "cleanup": "pending"}
        run.finished_at = now()
        if "error" in outcome:
            append_event(session, run.id, "error", outcome["error"])
        audit(
            session,
            run_id=run.id,
            action="run.finalize",
            decision=state,
            reason_code=state,
        )
