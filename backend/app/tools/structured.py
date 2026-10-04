"""Selected-source tools shared by file and database analytical runs."""

import asyncio
import csv
import hashlib
import io
import json
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.contracts import Contract, SafeError, ToolResult
from app.db.models import Artifact, Connection, Dataset, Evidence, Source, Thread, now
from app.policy.sql import SqlPolicyError, validate_sql
from app.sources.connections import QueryControl, execute_query, sample_dataset_rows
from app.sources.files import get_dataset_rows, profile_upload, working_csv
from app.storage.factory import get_storage
from app.tools.errors import ToolInputError
from app.tools.file_sql import query_program, table_alias

if TYPE_CHECKING:
    from app.agent.runtime import RunRuntime


class SourceInput(Contract):
    source_id: UUID


class DatasetInput(Contract):
    dataset_id: UUID


class SampleInput(DatasetInput):
    offset: int = Field(default=0, ge=0, le=1_000_000)
    limit: int = Field(default=20, ge=1, le=100)


class SQLInput(Contract):
    sql: str = Field(min_length=1, max_length=20000)
    source_id: UUID | None = None
    dataset_ids: list[UUID] = Field(default_factory=list, max_length=16)
    max_rows: int = Field(default=1000, ge=1, le=5000)
    timeout_seconds: int = Field(default=30, ge=1, le=30)


class RegisterInput(Contract):
    artifact_id: UUID
    display_name: str = Field(
        default="Derived result.csv", min_length=1, max_length=120
    )


class StructuredTools:
    def __init__(self, runtime: "RunRuntime"):
        self.runtime = runtime

    def selection(self) -> tuple[Any, list[Source], list[Dataset], str]:
        runtime = self.runtime
        with runtime.db() as session:
            run = runtime.guard(session)
            sources = list(
                session.scalars(
                    select(Source).where(Source.id.in_(run.selected_source_ids))
                )
            )
            ids = run.config.get("selected_dataset_ids", [])
            versions = run.config.get("source_versions", {})
            datasets = [
                item
                for item in session.scalars(
                    select(Dataset).where(
                        Dataset.source_id.in_(run.selected_source_ids)
                    )
                )
                if item.source_version == versions.get(item.source_id)
                and (not ids or item.id in ids)
            ]
            thread = session.get(Thread, run.thread_id)
            assert thread is not None
            return run, sources, datasets, thread.workspace_id

    async def execute(self, name: str, args: BaseModel, tool_id: str) -> ToolResult:
        run, sources, datasets, workspace_id = self.selection()
        if name == "list_sources":
            return ToolResult(
                status="ok",
                summary=f"{len(sources)} sources selected",
                data={
                    "sources": [
                        {
                            "id": item.id,
                            "name": item.display_name,
                            "kind": item.kind,
                            "version": run.config["source_versions"].get(item.id),
                            "state": item.state,
                            "description": item.description,
                            "metric_hints": item.metric_hints,
                            "datasets": [
                                {
                                    "id": row.id,
                                    "identity": row.identity,
                                    "sql_table": (
                                        table_alias(row.id)
                                        if item.kind
                                        in {"csv", "xlsx", "xls", "json", "parquet"}
                                        else row.identity
                                    ),
                                    "designation": row.designation,
                                }
                                for row in datasets
                                if row.source_id == item.id
                            ],
                        }
                        for item in sources
                    ]
                },
            )
        if name == "inspect_schema":
            assert isinstance(args, SourceInput)
            if str(args.source_id) not in {source.id for source in sources}:
                raise ToolInputError("source is not selected")
            entries = [
                self.profile(row)
                for row in datasets
                if row.source_id == str(args.source_id)
            ]
            return ToolResult(
                status="ok",
                summary=f"{len(entries)} selected datasets",
                data={"datasets": entries},
            )
        if name in {"dataset_profile", "sample_rows"}:
            assert isinstance(args, DatasetInput)
            dataset = next(
                (
                    row
                    for row in datasets
                    if row.id == str(args.dataset_id)
                    or row.source_id == str(args.dataset_id)
                ),
                None,
            )
            if dataset is None:
                raise ToolInputError(
                    f"Dataset '{args.dataset_id}' is not selected. Available dataset IDs: {sorted(row.id for row in datasets)}"
                )
            if name == "dataset_profile":
                return ToolResult(
                    status="ok",
                    summary=f"Profile for {dataset.identity}",
                    data=self.profile(dataset),
                )
            assert isinstance(args, SampleInput)
            source = next(row for row in sources if row.id == dataset.source_id)

            def sample() -> dict[str, Any]:
                with self.runtime.db() as session:
                    self.runtime.guard(session)
                    if source.kind in {"mysql", "postgresql"}:
                        return sample_dataset_rows(
                            session,
                            dataset,
                            self.runtime.settings,
                            offset=args.offset,
                            limit=args.limit,
                        )
                    rows = get_dataset_rows(
                        session,
                        get_storage(self.runtime.settings),
                        dataset.id,
                        offset=args.offset,
                        limit=args.limit,
                        max_bytes=self.runtime.settings.max_upload_bytes,
                    )
                    if rows is None:
                        raise ToolInputError("dataset disappeared")
                    return rows

            return ToolResult(
                status="ok",
                summary=f"Bounded row sample for {dataset.identity}",
                data=await asyncio.to_thread(sample),
            )
        if name == "register_dataset":
            assert isinstance(args, RegisterInput)
            with self.runtime.db() as session:
                live = self.runtime.guard(session)
                artifact = next(
                    (
                        row
                        for row in self.runtime.accessible_artifacts(session, live)
                        if row.id == str(args.artifact_id)
                    ),
                    None,
                )
                if artifact is None or artifact.media_type != "text/csv":
                    raise ToolInputError("choose an accessible CSV artifact")
            source_id, dataset_ids = await self.register(
                artifact, workspace_id, args.display_name, artifact.lineage
            )
            return ToolResult(
                status="ok",
                summary="Derived dataset registered without changing any original",
                artifact_ids=[args.artifact_id],
                data={
                    "source_id": source_id,
                    "dataset_ids": dataset_ids,
                    "selection_note": "Select this new source for a later run; current selected inputs are fixed.",
                },
            )
        assert name == "run_sql" and isinstance(args, SQLInput)
        return await self.query(args, tool_id, run, sources, datasets, workspace_id)

    @staticmethod
    def profile(dataset: Dataset) -> dict[str, Any]:
        details = dict(dataset.details)
        sample = details.get("sample")
        columns = details.get("columns", [])

        if isinstance(sample, list) and len(sample) > 2:
            max_sample_rows = 2 if len(columns) > 15 else 3
            details["sample"] = sample[:max_sample_rows]
            details["sample_note"] = (
                f"Showing {len(details['sample'])} preview rows of {len(columns)} columns. "
                "Use sample_rows tool for targeted row pagination."
            )

        return {
            "dataset_id": dataset.id,
            "identity": dataset.identity,
            "source_id": dataset.source_id,
            "source_version": dataset.source_version,
            "schema_version": dataset.schema_version,
            "sql_table": (
                dataset.identity
                if dataset.details.get("schema")
                else table_alias(dataset.id)
            ),
            "file_sql_types": (
                None
                if dataset.details.get("schema")
                else "VARCHAR; cast numeric/date values explicitly using schema hints"
            ),
            **details,
        }

    async def query(
        self,
        args: SQLInput,
        tool_id: str,
        run: Any,
        sources: list[Source],
        datasets: list[Dataset],
        workspace_id: str,
    ) -> ToolResult:
        source_id_to_dataset = {row.source_id: row.id for row in datasets}
        requested = {
            source_id_to_dataset.get(str(identity), str(identity))
            for identity in args.dataset_ids
        }
        if requested - {row.id for row in datasets}:
            raise ToolInputError(
                f"Query dataset is not selected. Available dataset IDs: {sorted(row.id for row in datasets)}"
            )
        chosen = [row for row in datasets if not requested or row.id in requested]
        source = (
            next((row for row in sources if row.id == str(args.source_id)), None)
            if args.source_id
            else None
        )
        if args.source_id and source is None:
            raise ToolInputError("query source is not selected")
        if source:
            chosen = [row for row in chosen if row.source_id == source.id]
        if not chosen:
            raise ToolInputError("select a dataset before querying")
        storage = get_storage(self.runtime.settings)
        if source and source.kind in {"mysql", "postgresql"}:
            with self.runtime.db() as session:
                self.runtime.guard(session)
                connection = session.scalar(
                    select(Connection).where(Connection.source_id == source.id)
                )
                if connection is None:
                    raise ToolInputError("connection not found")
            allowed = {row.identity for row in chosen}
            sql = validate_sql(args.sql, dialect=source.kind, allowed_tables=allowed)
            control = QueryControl()
            try:
                queried = await asyncio.to_thread(
                    execute_query,
                    connection,
                    self.runtime.settings,
                    sql,
                    allowed,
                    args.max_rows,
                    args.timeout_seconds,
                    control,
                )
            except asyncio.CancelledError:
                await asyncio.to_thread(control.cancel)
                raise
            columns = queried.columns
            rows = queried.rows
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
            content = buffer.getvalue().encode()
            if len(content) > self.runtime.settings.max_upload_bytes:
                raise ToolInputError("SQL result exceeds artifact size limit")
            stored = await asyncio.to_thread(
                storage.put,
                f"derived/{workspace_id}/{run.id}/{tool_id}/result.csv",
                content,
            )
            artifact_id = str(uuid4())
            descriptors = [
                {
                    "id": artifact_id,
                    "display_name": "result.csv",
                    "storage_key": stored.key,
                    "media_type": "text/csv",
                    "byte_size": stored.byte_size,
                    "sha256": stored.sha256,
                }
            ]
            metadata = {
                "columns": columns,
                "rows": [
                    {
                        key: value[:2000] if isinstance(value, str) else value
                        for key, value in row.items()
                    }
                    for row in rows[:20]
                ],
                "row_count": queried.row_count,
                "truncated": queried.truncated,
                "column_mapping": queried.column_mapping,
                "truncated_reason": queried.truncated_reason,
            }
            result = ToolResult(
                status="partial" if queried.truncated else "ok",
                summary=f"Query returned {queried.row_count} retained rows; output limit {args.max_rows}",
                artifact_ids=[UUID(artifact_id)],
                data={"artifacts": descriptors},
            )
        else:
            file_sources = {
                row.id: row
                for row in sources
                if row.kind in {"csv", "xlsx", "xls", "json", "parquet"}
            }
            if any(row.source_id not in file_sources for row in chosen):
                raise ToolInputError(
                    "choose a database source_id or file datasets; databases cannot share a guest connection"
                )
            allowed = {table_alias(row.id) for row in chosen}
            sql = validate_sql(args.sql, dialect="duckdb", allowed_tables=allowed)
            from app.agent.runtime import PythonInput

            result = await self.runtime.run_python(
                workspace_id,
                run.id,
                tool_id,
                PythonInput(
                    code=query_program(
                        sql,
                        [row.id for row in chosen],
                        args.max_rows,
                        args.timeout_seconds,
                    ),
                    input_dataset_ids=[UUID(row.id) for row in chosen],
                    output_paths=["result.csv", "query-result.json"],
                    timeout_seconds=min(300, args.timeout_seconds + 15),
                ),
            )
            if result.status not in {"ok", "partial"}:
                return result
            descriptors = result.data.get("artifacts", [])
            output = next(
                (row for row in descriptors if row["display_name"] == "result.csv"),
                None,
            )
            state = next(
                (
                    row
                    for row in descriptors
                    if row["display_name"] == "query-result.json"
                ),
                None,
            )
            if output is None or state is None:
                return ToolResult(
                    status="failed",
                    summary="File SQL did not retain its result and metadata",
                    artifact_ids=result.artifact_ids,
                    error=SafeError(
                        code="sql_result_missing",
                        message="Inspect retained code and retry with a narrower query",
                    ),
                    data=result.data,
                )
            artifact_id = output["id"]
            metadata = json.loads(
                await asyncio.to_thread(
                    storage.read,
                    state["storage_key"],
                    self.runtime.settings.max_result_bytes,
                )
            )
            result.status = "partial" if metadata["truncated"] else "ok"
            result.summary = f"Query returned {metadata['row_count']} retained rows; output limit {args.max_rows}"
        query_artifact = await asyncio.to_thread(
            storage.put,
            f"derived/{workspace_id}/{run.id}/{tool_id}/query.sql",
            sql.encode(),
        )
        query_id = str(uuid4())
        descriptors.append(
            {
                "id": query_id,
                "display_name": "query.sql",
                "storage_key": query_artifact.key,
                "media_type": "text/plain",
                "byte_size": query_artifact.byte_size,
                "sha256": query_artifact.sha256,
            }
        )
        result.artifact_ids.append(UUID(query_id))
        source_ids = list(dict.fromkeys(row.source_id for row in chosen))
        output = next(row for row in descriptors if row["id"] == artifact_id)
        evidence_id = str(uuid4())
        with self.runtime.db() as session, session.begin():
            self.runtime.guard(session)
            session.add(
                Evidence(
                    id=evidence_id,
                    run_id=run.id,
                    kind="structured",
                    source_ids=source_ids,
                    details={
                        "source_versions": {
                            identity: run.config["source_versions"][identity]
                            for identity in source_ids
                        },
                        "dataset_ids": [row.id for row in chosen],
                        "schema_versions": {
                            row.id: row.schema_version for row in chosen
                        },
                        "query": sql,
                        "query_artifact_id": query_id,
                        "code_artifact_id": result.data.get("code_artifact_id"),
                        "executed_at": now().isoformat(),
                        "result_artifact_id": artifact_id,
                        "result_sha256": output["sha256"],
                        "row_limit": args.max_rows,
                        "truncated": metadata["truncated"],
                        "limit_semantics": "retained output only; aggregation reads full input",
                        "units": {
                            column["name"]: column["hints"]["unit"]
                            for row in chosen
                            for column in row.details.get("columns", [])
                            if isinstance(column, dict)
                            and isinstance(column.get("hints"), dict)
                            and column["hints"].get("unit")
                        },
                        "assumptions": [],
                    },
                )
            )
        result.evidence_ids = [UUID(evidence_id)]
        result.data = {
            "code_artifact_id": result.data.get("code_artifact_id"),
            "staged_input_artifacts": result.data.get("staged_input_artifacts", []),
            "input_artifact_ids": result.data.get("input_artifact_ids", []),
            "input_lineage": result.data.get("input_lineage", []),
            "input_dataset_ids": result.data.get("input_dataset_ids", []),
            "artifacts": descriptors,
            **metadata,
            "query": sql,
            "query_artifact_id": query_id,
            "sample_note": "Only the first 20 returned rows are shown; retained CSV contains the bounded full result",
        }
        return result

    async def register(
        self,
        artifact: Artifact,
        workspace_id: str,
        display_name: str,
        lineage: list[str],
    ) -> tuple[str, list[str]]:
        storage = get_storage(self.runtime.settings)
        content = await asyncio.to_thread(
            storage.read, artifact.storage_key, self.runtime.settings.max_upload_bytes
        )
        if hashlib.sha256(content).hexdigest() != artifact.sha256:
            raise ToolInputError("artifact integrity check failed")
        profiles = await asyncio.to_thread(profile_upload, "result.csv", content)
        with self.runtime.db() as session, session.begin():
            self.runtime.guard(session)
            existing = next(
                (
                    row
                    for row in session.scalars(
                        select(Source).where(Source.workspace_id == workspace_id)
                    )
                    if row.details.get("artifact_id") == artifact.id
                ),
                None,
            )
            if existing:
                return existing.id, list(
                    session.scalars(
                        select(Dataset.id).where(Dataset.source_id == existing.id)
                    )
                )
            source = Source(
                workspace_id=workspace_id,
                kind="csv",
                display_name=display_name,
                state="ready",
                storage_key=artifact.storage_key,
                content_hash=artifact.sha256,
                schema_version="derived-csv-v1",
                details={
                    "designation": "derived",
                    "artifact_id": artifact.id,
                    "producer_run_id": artifact.run_id,
                    "lineage": lineage,
                },
            )
            session.add(source)
            session.flush()
            ids = []
            for profile in profiles:
                dataset = Dataset(
                    source_id=source.id,
                    source_version=source.version,
                    identity=str(profile.get("sheet_name") or "result"),
                    schema_version=hashlib.sha256(
                        json.dumps(profile, sort_keys=True).encode()
                    ).hexdigest(),
                    details=profile,
                    storage_key=artifact.storage_key,
                    designation="derived",
                    lineage=[*lineage, f"artifact:{artifact.id}"],
                )
                session.add(dataset)
                session.flush()
                ids.append(dataset.id)
            return source.id, ids
