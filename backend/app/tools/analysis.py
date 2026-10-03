"""Validated operations executed through the existing microVM runner."""

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

from sqlalchemy import select

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

from app.contracts import SafeError, ToolResult
from app.db.models import Evidence, now
from app.storage.factory import get_storage
from app.tools.errors import ToolInputError

if TYPE_CHECKING:
    from app.agent.runtime import RunRuntime


class AnalysisSource(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    alias: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    dataset_id: UUID | None = None
    artifact_id: UUID | None = None
    units: dict[str, str] = Field(default_factory=dict, max_length=32)

    @model_validator(mode="after")
    def exactly_one(self) -> "AnalysisSource":
        if (self.dataset_id is None) == (self.artifact_id is None):
            raise ValueError("Specify exactly one dataset or CSV snapshot artifact")
        if any(
            not k or len(k) > 128 or not v or len(v) > 64 for k, v in self.units.items()
        ):
            raise ValueError("Unit names exceed bounds")
        return self


class Metric(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    column: str = Field(min_length=1, max_length=128)
    function: Literal["sum", "mean", "min", "max", "count"]
    output: str = Field(min_length=1, max_length=128)


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal[
        "convert",
        "filter",
        "missing",
        "duplicates",
        "join",
        "aggregate",
        "derive",
        "melt",
        "pivot",
        "statistics",
    ]
    frame: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    columns: list[str] = Field(default_factory=list, max_length=32)
    data_type: Literal["text", "number", "integer", "date"] = "text"
    date_format: str = Field(default="%Y-%m-%d", max_length=64)
    policy: Literal["reject", "drop", "fill"] = "reject"
    value: str | int | float | None = None
    comparison: Literal["eq", "ne", "gt", "ge", "lt", "le"] | None = None
    right_frame: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,31}$")
    right_on: list[str] = Field(default_factory=list, max_length=32)
    how: Literal["left", "inner"] = "left"
    relationship: Literal[
        "one_to_one", "one_to_many", "many_to_one", "many_to_many"
    ] = "many_to_one"
    metrics: list[Metric] = Field(default_factory=list, max_length=32)
    output: str | None = Field(default=None, min_length=1, max_length=128)
    function: Literal[
        "add",
        "subtract",
        "multiply",
        "divide",
        "year",
        "month",
        "fiscal_year",
        "describe",
        "correlation",
        "regression",
        "outliers",
    ] = "describe"
    fiscal_start_month: int = Field(default=4, ge=1, le=12)
    value_columns: list[str] = Field(default_factory=list, max_length=32)
    unit: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def required_fields(self) -> "Operation":
        if self.kind != "aggregate" and not self.columns:
            raise ValueError("Operation requires columns")
        for name in [*self.columns, *self.right_on, *self.value_columns]:
            if not name or len(name) > 128:
                raise ValueError("Column names must have 1 to 128 characters")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("Duplicate columns")
        if self.kind == "join" and (
            not self.right_frame or len(self.right_on) != len(self.columns)
        ):
            raise ValueError("Join requires a right frame and equal key counts")
        if self.kind == "aggregate" and (
            not self.metrics
            or len({m.output for m in self.metrics}) != len(self.metrics)
            or any(m.output in self.columns for m in self.metrics)
        ):
            raise ValueError("Aggregation requires unique metric output names")
        if self.kind == "filter" and (
            len(self.columns) != 1 or self.value is None or self.comparison is None
        ):
            raise ValueError(
                "Filter requires one column, a value and explicit comparison; le means inclusive <=, eq means exact equality"
            )
        if self.kind == "missing" and self.policy == "fill" and self.value is None:
            raise ValueError("Fill requires an explicit value")
        if self.kind == "duplicates" and self.policy == "fill":
            raise ValueError("Duplicates support reject or drop")
        if self.kind == "derive":
            expected = 1 if self.function in {"year", "month", "fiscal_year"} else 2
            if (
                self.function
                not in {
                    "year",
                    "month",
                    "fiscal_year",
                    "add",
                    "subtract",
                    "multiply",
                    "divide",
                }
                or len(self.columns) != expected
                or not self.output
            ):
                raise ValueError(
                    "Derived columns require an output and compatible function/columns"
                )
        if self.kind == "statistics" and (
            self.function not in {"describe", "correlation", "regression", "outliers"}
            or (self.function == "outliers" and len(self.columns) != 1)
        ):
            raise ValueError("Unsupported statistical operation")
        if self.kind == "melt" and (
            not self.value_columns
            or set(self.columns) & set(self.value_columns)
            or {"variable", "value"} & set(self.columns)
        ):
            raise ValueError("Melt requires disjoint identifier and value columns")
        if self.kind == "pivot" and len(self.value_columns) != 2:
            raise ValueError("Pivot requires name and value columns")
        return self


class ChartInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: Literal["bar", "scatter"] = "bar"
    x: str = Field(min_length=1, max_length=128)
    y: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    unit: str = Field(default="", max_length=64)

    @field_validator("x", "y", "title", "unit")
    @classmethod
    def plain_labels(cls, value: str) -> str:
        from app.artifacts.chart import _safe_label

        return _safe_label(value)


class AnalyzeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    inputs: list[AnalysisSource] = Field(min_length=1, max_length=16)
    operations: list[Operation] = Field(default_factory=list, max_length=20)
    result_frame: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    max_rows: int = Field(default=5000, ge=1, le=10000)
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    chart: ChartInput | None = None

    @model_validator(mode="after")
    def references(self) -> "AnalyzeInput":
        aliases = {i.alias for i in self.inputs}
        if len(aliases) != len(self.inputs) or self.result_frame not in aliases:
            raise ValueError("Input aliases must be unique and result frame must exist")
        if any(
            op.frame not in aliases
            or (op.kind == "join" and op.right_frame not in aliases)
            for op in self.operations
        ):
            raise ValueError("Unknown analysis frame")
        if (
            any(i.units for i in self.inputs) or any(op.unit for op in self.operations)
        ) and not self.assumptions:
            raise ValueError("Explicit unit declarations require recorded assumptions")
        if any(len(a) > 500 for a in self.assumptions):
            raise ValueError("Assumptions exceed text bounds")
        return self


class AnalysisTools:
    def __init__(self, runtime: "RunRuntime"):
        self.runtime = runtime

    async def execute(self, args: AnalyzeInput, tool_id: str) -> ToolResult:
        from app.agent.runtime import PythonInput
        from app.tools.structured import StructuredTools

        run, sources, datasets, workspace_id = StructuredTools(self.runtime).selection()
        config = args.model_dump(mode="json")
        snapshot_refs: list[dict[str, object]] = []
        used_sources: set[str] = set()
        with self.runtime.db() as session:
            current = self.runtime.guard(session)
            accessible = self.runtime.accessible_artifacts(session, current)
            for item, spec in zip(args.inputs, config["inputs"], strict=True):
                spec["units"] = {}
                if item.dataset_id:
                    dataset = next(
                        (d for d in datasets if d.id == str(item.dataset_id)), None
                    )
                    if dataset is None:
                        raise ToolInputError("Dataset must be selected")
                    source = next(s for s in sources if s.id == dataset.source_id)
                    used_sources.add(source.id)
                    if source.kind in {"mysql", "postgresql"}:
                        raise ToolInputError(
                            "Fetch a bounded run_sql snapshot before analysis"
                        )
                    spec["path"] = f"{dataset.id}.csv"
                    spec["units"] = {
                        c["name"]: c["hints"]["unit"]
                        for c in dataset.details.get("columns", [])
                        if c.get("hints", {}).get("unit")
                    }
                    snapshot_refs.append(
                        {
                            "dataset_id": dataset.id,
                            "source_id": source.id,
                            "source_version": source.version,
                            "schema_version": dataset.schema_version,
                            "lineage": dataset.lineage or [],
                        }
                    )
                else:
                    artifact = next(
                        (a for a in accessible if a.id == str(item.artifact_id)), None
                    )
                    if artifact is None or artifact.media_type != "text/csv":
                        raise ToolInputError(
                            "Analysis artifact must be an accessible CSV snapshot"
                        )
                    spec["path"] = artifact.id
                    producer_evidence = next(
                        (
                            e
                            for e in session.scalars(
                                select(Evidence).where(
                                    Evidence.run_id == artifact.run_id
                                )
                            )
                            if e.details.get("result_artifact_id") == artifact.id
                        ),
                        None,
                    )
                    if producer_evidence:
                        used_sources.update(producer_evidence.source_ids)
                        spec["units"] = producer_evidence.details.get("units", {})
                    else:
                        used_sources.update(
                            s.id
                            for s in sources
                            if s.id in artifact.lineage
                            or any(
                                ref.startswith(f"source:{s.id}@")
                                for ref in artifact.lineage
                            )
                        )
                    snapshot_refs.append(
                        {
                            "artifact_id": artifact.id,
                            "sha256": artifact.sha256,
                            "lineage": artifact.lineage,
                            "evidence_id": (
                                producer_evidence.id if producer_evidence else None
                            ),
                            "truncated": (
                                producer_evidence.details.get("truncated")
                                if producer_evidence
                                else None
                            ),
                            "row_limit": (
                                producer_evidence.details.get("row_limit")
                                if producer_evidence
                                else None
                            ),
                            "snapshot_limit": "May be a bounded query or prior analysis result. Inspect its producing evidence.",
                        }
                    )
                for col, unit in item.units.items():
                    if spec["units"].get(col) and spec["units"][col] != unit:
                        raise ToolInputError(
                            "Explicit units conflict with source units"
                        )
                    spec["units"][col] = unit
        code = (
            Path(__file__).with_name("analysis_engine.py").read_text()
            + "\nrun_analysis(json.loads("
            + repr(json.dumps(config))
            + "))\n"
        )
        paths = ["result.csv", "analysis.json"] + (
            ["chart.json", "chart.png"] if args.chart else []
        )
        result = await self.runtime.run_python(
            workspace_id,
            run.id,
            tool_id,
            PythonInput(
                code=code,
                output_paths=paths,
                input_dataset_ids=[i.dataset_id for i in args.inputs if i.dataset_id],
                input_artifact_ids=[
                    i.artifact_id for i in args.inputs if i.artifact_id
                ],
            ),
        )
        descriptors = result.data.get("artifacts", [])
        metadata_file = next(
            (a for a in descriptors if a.get("relative_path") == "analysis.json"), None
        )
        if metadata_file is None:
            return result
        content = await asyncio.to_thread(
            get_storage(self.runtime.settings).read,
            metadata_file["storage_key"],
            1024 * 1024,
        )
        metadata = json.loads(content)
        metadata["analysis_version"] = "analysis-engine-v1"
        metadata["sandbox_image"] = self.runtime.settings.sandbox_image
        if any(ref.get("truncated") for ref in snapshot_refs):
            metadata["limitations"].append(
                "At least one input is a truncated snapshot; downstream analysis cannot recover omitted rows."
            )
            metadata["input_truncated"] = True
        result.data.update(
            analysis=metadata,
            input_snapshots=snapshot_refs,
            input_artifact_ids=[
                str(i.artifact_id) for i in args.inputs if i.artifact_id
            ],
        )
        if metadata["status"] != "ok":
            result.status = "failed"
            result.summary = "Analysis requires corrected inputs or clarification"
            result.error = SafeError(
                code="analysis_rejected",
                message=str(metadata.get("error", "analysis_failed"))[:200],
            )
            return result
        if args.chart:
            from app.artifacts.chart import ChartSpec

            chart_file = next(
                (a for a in descriptors if a.get("relative_path") == "chart.json"), None
            )
            try:
                if chart_file is None:
                    raise ToolInputError("chart unavailable")
                chart_bytes = await asyncio.to_thread(
                    get_storage(self.runtime.settings).read,
                    chart_file["storage_key"],
                    1024 * 1024,
                )
                ChartSpec.from_json_bytes(chart_bytes)
            except ValueError:
                result.status = "failed"
                result.summary = (
                    "Chart labels or values do not satisfy the safe chart contract"
                )
                result.error = SafeError(
                    code="chart_rejected",
                    message="Use bounded plain text labels and finite numeric values",
                )
                return result
        output = next(a for a in descriptors if a.get("relative_path") == "result.csv")
        evidence_id = str(uuid4())
        with self.runtime.db() as session, session.begin():
            self.runtime.guard(session)
            session.add(
                Evidence(
                    id=evidence_id,
                    run_id=run.id,
                    kind="structured",
                    source_ids=sorted(used_sources),
                    details={
                        **metadata,
                        "source_versions": {
                            identity: version
                            for identity, version in run.config[
                                "source_versions"
                            ].items()
                            if identity in used_sources
                        },
                        "dataset_ids": [
                            str(i.dataset_id) for i in args.inputs if i.dataset_id
                        ],
                        "input_snapshots": snapshot_refs,
                        "code_artifact_id": result.data.get("code_artifact_id"),
                        "result_artifact_id": output["id"],
                        "result_sha256": output["sha256"],
                        "executed_at": now().isoformat(),
                        "limit_semantics": "operations read complete staged input; output retention only is capped",
                    },
                )
            )
        result.evidence_ids = [UUID(evidence_id)]
        result.status = (
            "partial"
            if metadata["truncated"]
            or metadata.get("chart_truncated")
            or metadata.get("input_truncated")
            else "ok"
        )
        result.summary = f"Calculated {metadata['row_count']} rows; retained {metadata['retained_rows']}. Inspect analysis metadata for join diagnostics and assumptions."
        return result
