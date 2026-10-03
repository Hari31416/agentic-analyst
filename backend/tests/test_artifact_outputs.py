import csv
import base64
import io
import json
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from openpyxl import load_workbook
from pypdf import PdfReader

from app.artifacts.chart import ChartSpec
from app.artifacts.tabular import decode_table, safe_csv, safe_parquet, safe_xlsx
from app.config import Settings
from app.db.models import (
    Artifact,
    Base,
    Evidence,
    Run,
    Source,
    Thread,
    Workspace,
    ToolCall,
)
from app.storage.filesystem import FileStorage
from app.tools.reports import (
    GenerateReportInput,
    ReportsTool,
    _InputSnapshot,
    _notebook_bytes,
    _pdf_bytes,
)
from app.tools.python import _media_type


def test_chart_schema_rejects_executable_plotly_extensions() -> None:
    with pytest.raises(ValueError):
        ChartSpec.model_validate(
            {
                "schema_version": 1,
                "data": [
                    {"type": "scatter", "x": [1], "y": [2], "hovertemplate": "<script>"}
                ],
            }
        )
    with pytest.raises(ValueError):
        ChartSpec.model_validate(
            {
                "schema_version": 1,
                "data": [{"type": "bar", "x": ["<img src=x>"], "y": [1]}],
            }
        )
    spec = ChartSpec.model_validate(
        {"schema_version": 1, "data": [{"type": "bar", "x": ["जिला A"], "y": [1.0]}]}
    )
    assert spec.plotly()["data"][0]["type"] == "bar"


def test_guest_artifact_media_types_are_typed_from_content() -> None:
    chart = json.dumps(
        {"schema_version": 1, "data": [{"type": "bar", "x": ["A"], "y": [1]}]}
    ).encode()
    assert _media_type("chart.json", chart) == "application/vnd.plotly.v1+json"
    assert (
        _media_type("chart.json", b'{"data":[{"type":"scatter","hovertemplate":"x"}]}')
        == "application/json"
    )
    assert (
        _media_type("data.parquet", b"PAR1unsafePAR1")
        == "application/vnd.apache.parquet"
    )
    assert _media_type("notes.xlsx", b"PK\x03\x04invalid") == "application/octet-stream"


def test_spreadsheet_exports_escape_formulas_and_preserve_large_values() -> None:
    csv_bytes = safe_csv(
        ["label", "amount", "identifier"],
        [
            ["=1+1", "-12.50", "00123456789012345678"],
            ["@SUM(A1:A2)", "99999999999999999", "safe"],
        ],
    )
    rows = list(csv.reader(io.StringIO(csv_bytes.decode())))
    assert rows[1] == ["'=1+1", "-12.50", "00123456789012345678"]
    assert rows[2][0] == "'@SUM(A1:A2)"

    workbook = load_workbook(
        io.BytesIO(
            safe_xlsx(
                ["name", "n", "identifier"],
                [["=cmd|' /C calc'!A0", 12345678901234567, "00123"]],
            )
        ),
        data_only=False,
    )
    sheet = workbook.active
    assert sheet is not None
    assert sheet["A2"].data_type == "s"
    assert sheet["A2"].value.startswith("'")
    assert sheet["B2"].data_type == "s"
    assert sheet["B2"].value == "12345678901234567"
    assert sheet["C2"].data_type == "s"
    assert sheet["C2"].value == "00123"


def test_table_pagination_keeps_cells_as_strings() -> None:
    data = b"name,value\n0012,12345678901234567890\n\n"
    columns, rows = decode_table(data, "result.csv")
    assert columns == ["name", "value"]
    assert rows[0] == ["0012", "12345678901234567890"]


def test_json_and_parquet_keep_exact_decimal_strings_and_reject_nonfinite() -> None:
    columns, rows = decode_table(
        b'{"columns":["amount","count"],"rows":[{"amount":12345678901234567890.123400,"count":9007199254740993}]}',
        "result.json",
    )
    assert columns == ["amount", "count"]
    assert str(rows[0][0]) == "12345678901234567890.123400"
    assert rows[0][1] == 9007199254740993
    with pytest.raises(ValueError, match="non-finite"):
        decode_table(b'{"columns":["n"],"rows":[{"n":NaN}]}', "bad.json")

    parquet = safe_parquet(
        ["amount", "identifier"], [["12345678901234567890.1200", "00123"]]
    )
    columns, rows = decode_table(parquet, "result.parquet")
    assert columns == ["amount", "identifier"]
    assert rows == [["12345678901234567890.1200", "00123"]]


def test_notebook_has_exact_code_snapshot_hashes_and_safe_markdown() -> None:
    source = b"print('retained code')\n"
    digest = __import__("hashlib").sha256(source).hexdigest()
    code_artifact = Artifact(
        id=str(uuid4()),
        run_id=str(uuid4()),
        storage_key="code.py",
        display_name="analysis.py",
        media_type="text/x-python",
        byte_size=len(source),
        sha256=digest,
        lineage=[],
        durable=True,
    )
    snapshot_bytes = b"x,y\n1,2\n"
    snapshot = Artifact(
        id=str(uuid4()),
        run_id=code_artifact.run_id,
        storage_key="input.csv",
        display_name="inputs.csv",
        media_type="text/csv",
        byte_size=len(snapshot_bytes),
        sha256=__import__("hashlib").sha256(snapshot_bytes).hexdigest(),
        lineage=[],
        durable=True,
    )
    evidence = Evidence(
        id=str(uuid4()),
        run_id=code_artifact.run_id,
        kind="document",
        source_ids=[str(uuid4())],
        details={
            "excerpt": "<script>alert(1)</script> [link](javascript:alert(1))",
            "location": {"page": 2},
        },
    )
    notebook = json.loads(
        _notebook_bytes(
            "सारांश",
            [(code_artifact, source)],
            [_InputSnapshot(snapshot, snapshot_bytes)],
            [],
            [(evidence, "अनुदान.pdf")],
            ["ठीक"],
            [],
        )
    )
    code_cell = next(cell for cell in notebook["cells"] if cell["cell_type"] == "code")
    assert code_cell["source"] == [source.decode()]
    assert (
        notebook["metadata"]["agentic_rag"]["input_snapshots"][0]["sha256"]
        == snapshot.sha256
    )
    markdown_text = "".join(
        "".join(cell["source"])
        for cell in notebook["cells"]
        if cell["cell_type"] == "markdown"
    )
    assert "<script>" not in markdown_text
    assert "javascript:" not in markdown_text


def test_bilingual_pdf_has_text_and_pages() -> None:
    result = Artifact(
        id=str(uuid4()),
        run_id=str(uuid4()),
        storage_key="result.csv",
        display_name="résultats.csv",
        media_type="text/csv",
        byte_size=0,
        sha256="a" * 64,
        lineage=[],
        durable=True,
    )
    evidence = Evidence(
        id=str(uuid4()),
        run_id=result.run_id,
        kind="document",
        source_ids=[str(uuid4())],
        details={
            "excerpt": "योजना में कुल 1,25,000 रुपये स्वीकृत हुए। The result is 125000 INR.",
            "location": {"page": 3},
        },
    )
    pdf = _pdf_bytes(
        "वित्तीय विश्लेषण / Finance report",
        [(result, ["district", "total INR"], [["वाराणसी", "125000"]], 1)],
        [(evidence, "अनुदान नीति.pdf")],
        ["Exact source total retained"],
        [],
    )
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 1
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Finance report" in text
    assert "125000" in text


def test_report_tool_replays_retained_sources_and_persists_lineage(
    tmp_path, monkeypatch
) -> None:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    storage = FileStorage(tmp_path)
    settings = Settings(_env_file=None, storage_root=tmp_path)
    monkeypatch.setattr("app.tools.reports.get_storage", lambda _settings: storage)

    class Runtime:
        def __init__(self) -> None:
            self.settings = settings

        @contextmanager
        def db(self):
            with sessions() as session:
                yield session

        @staticmethod
        def guard(session):
            return session.get(Run, run_id)

        @staticmethod
        def accessible_artifacts(session, run):
            return list(
                session.scalars(
                    select(Artifact)
                    .join(Run, Artifact.run_id == Run.id)
                    .where(Run.thread_id == run.thread_id)
                )
            )

    with sessions() as session, session.begin():
        workspace = Workspace(label="reports")
        session.add(workspace)
        session.flush()
        thread = Thread(workspace_id=workspace.id, label="report thread")
        session.add(thread)
        session.flush()
        source = Source(
            workspace_id=workspace.id,
            kind="pdf",
            display_name="Policy.pdf",
            state="ready",
            version=1,
        )
        session.add(source)
        session.flush()
        run = Run(
            thread_id=thread.id,
            state="completed",
            selected_source_ids=[source.id],
            config={"source_versions": {source.id: 1}},
        )
        session.add(run)
        session.flush()
        run_id = run.id
        call = ToolCall(
            run_id=run.id,
            provider_call_id="analysis-call",
            name="analyze_data",
            input_reference={},
            decision="allowed",
            status="completed",
        )
        session.add(call)
        session.flush()
        result_bytes = "district,total INR\nवाराणसी,125000.00\n".encode()
        code_bytes = b"print('exact retained program')\n"
        input_bytes = "district,total INR\nवाराणसी,125000.00\n".encode()
        result_store = storage.put(f"derived/{run.id}/result.csv", result_bytes)
        code_store = storage.put(f"derived/{run.id}/code.py", code_bytes)
        input_store = storage.put(f"derived/{run.id}/input.csv", input_bytes)
        result = Artifact(
            run_id=run.id,
            tool_call_id=call.id,
            storage_key=result_store.key,
            display_name="result.csv",
            media_type="text/csv",
            byte_size=result_store.byte_size,
            sha256=result_store.sha256,
            lineage=[f"source:{source.id}@1"],
        )
        code = Artifact(
            run_id=run.id,
            tool_call_id=call.id,
            storage_key=code_store.key,
            display_name="code.py",
            media_type="text/x-python",
            byte_size=code_store.byte_size,
            sha256=code_store.sha256,
            lineage=[f"source:{source.id}@1"],
        )
        input_artifact = Artifact(
            run_id=run.id,
            tool_call_id=call.id,
            storage_key=input_store.key,
            display_name="input-dataset.csv",
            media_type="text/csv",
            byte_size=input_store.byte_size,
            sha256=input_store.sha256,
            lineage=[f"source:{source.id}@1"],
        )
        session.add_all([result, code, input_artifact])
        session.flush()
        call.result = {
            "data": {
                "code_artifact_id": code.id,
                "staged_input_artifacts": [
                    {
                        "id": input_artifact.id,
                        "guest_path": "/workspace/inputs/source.csv",
                    }
                ],
            }
        }
        evidence = Evidence(
            run_id=run.id,
            kind="document",
            source_ids=[source.id],
            details={
                "excerpt": "वाराणसी के लिए अनुदान INR 125000.00 है।",
                "location": {"page": 2},
                "source_versions": {source.id: 1},
            },
        )
        session.add(evidence)
        session.flush()
        result_id, evidence_id = result.id, evidence.id

    try:
        generated = ReportsTool(Runtime())._generate(
            GenerateReportInput(
                title="District result",
                artifact_ids=[result_id],
                evidence_ids=[evidence_id],
            ),
            str(uuid4()),
        )
        assert generated.status == "ok"
        assert len(generated.artifact_ids) == 3
        descriptors = generated.data["artifacts"]
        markdown = storage.read(
            next(
                item["storage_key"]
                for item in descriptors
                if item["display_name"].endswith(".md")
            )
        ).decode()
        assert "125000.00" in markdown and "page" in markdown
        notebook = json.loads(
            storage.read(
                next(
                    item["storage_key"]
                    for item in descriptors
                    if item["display_name"].endswith(".ipynb")
                )
            )
        )
        code_cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
        assert code_cells[0]["source"] == [code_bytes.decode()]
        snapshots = notebook["metadata"]["agentic_rag"]["input_snapshots"]
        assert snapshots[0]["sandbox_guest_path"] == "/workspace/inputs/source.csv"
        assert base64.b64decode(snapshots[0]["embedded_base64"]) == input_bytes
        assert all(len(item["lineage"]) == 4 for item in descriptors)
        assert f"evidence:{evidence_id}" in descriptors[0]["lineage"]
    finally:
        engine.dispose()
