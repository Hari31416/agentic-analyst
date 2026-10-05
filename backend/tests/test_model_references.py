import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.agent.loop import AgentLoop, Tool
from app.agent.protocol import ModelResponse, ModelToolCall
from app.agent.references import ModelReferences
from app.config import Settings
from app.contracts import ToolResult
from app.policy.decisions import tool_decision
from app.policy.sql import SqlPolicyError, validate_sql
from app.portability.service import _remap_json, _safe_run_config
from app.tools.analysis import AnalyzeInput
from app.tools.file_sql import query_program, table_alias
from app.tools.structured import DatasetInput, SQLInput, StructuredTools


def test_stable_typed_mapping_roundtrip_and_no_reuse():
    identity = str(uuid4())
    refs = ModelReferences()
    assert refs.reference("dataset", identity) == "dataset_1"
    assert refs.reference("source", identity) == "source_1"
    restored = ModelReferences(refs.aliases)
    assert restored.reference("dataset", identity) == "dataset_1"
    assert restored.reference("dataset", str(uuid4())) == "dataset_2"
    assert restored.resolve("dataset", "dataset_1") == identity
    with pytest.raises(ValueError, match="Unknown dataset reference"):
        restored.resolve("dataset", "source_1")
    with pytest.raises(ValueError, match="Unknown evidence reference"):
        restored.resolve("evidence", "e999")
    assert (
        ModelReferences({"e9": identity}).reference("evidence", str(uuid4())) == "e10"
    )


@pytest.mark.parametrize("mapping", [{"e0": str(uuid4())}, {"e1": "bad"}, {"e1": None}])
def test_invalid_persisted_maps_fail_explicitly(mapping):
    with pytest.raises(ValueError, match="invalid retained"):
        ModelReferences(mapping)


def test_model_view_shortens_references_without_changing_source_values():
    dataset, source, artifact, evidence, chunk = [str(uuid4()) for _ in range(5)]
    refs = ModelReferences()
    source_values = {"dataset_id": dataset, "evidence_id": evidence, "value": "[e99]"}
    result = ToolResult(
        status="ok",
        summary="Available resources",
        artifact_ids=[artifact],
        evidence_ids=[evidence],
        data={
            "sources": [
                {
                    "id": source,
                    "name": "sales.csv",
                    "datasets": [{"id": dataset, "sql_table": table_alias(dataset)}],
                }
            ],
            "artifacts": [{"id": artifact}],
            "passages": [
                {
                    "chunk_id": chunk,
                    "evidence_id": evidence,
                    "source_id": source,
                    "excerpt": json.dumps(source_values),
                }
            ],
            "rows": [source_values],
            "sample": [source_values],
            "staged_input_artifacts": [
                {
                    "id": artifact,
                    "dataset_id": dataset,
                    "guest_path": f"/workspace/inputs/{dataset}.csv",
                }
            ],
        },
    )
    view = json.loads(refs.result(result))
    assert view["data"]["sources"][0]["id"] == "source_1"
    assert view["data"]["sources"][0]["datasets"][0] == {
        "id": "dataset_1",
        "sql_table": "dataset_1",
    }
    assert view["data"]["artifacts"][0]["id"] == "artifact_1"
    assert view["data"]["passages"][0]["chunk_id"] == "chunk_1"
    assert view["data"]["passages"][0]["evidence_id"] == "e1"
    assert view["data"]["passages"][0]["excerpt"] == json.dumps(source_values)
    assert view["data"]["rows"] == [source_values]
    assert (
        view["data"]["staged_input_artifacts"][0]["guest_path"]
        == "/workspace/inputs/dataset_1.csv"
    )
    assert result.data["sources"][0]["id"] == source


def test_nested_tool_arguments_schemas_and_policy_still_use_uuid_contracts():
    dataset, artifact = [str(uuid4()) for _ in range(2)]
    refs = ModelReferences({"dataset_1": dataset, "artifact_1": artifact})
    payload = {
        "result_frame": "sales",
        "inputs": [{"alias": "sales", "dataset_id": "dataset_1"}],
        "operations": [
            {
                "kind": "aggregate",
                "frame": "sales",
                "metrics": [
                    {"column": "revenue", "function": "sum", "output": "total"}
                ],
            }
        ],
    }
    args = AnalyzeInput.model_validate(refs.arguments(json.dumps(payload)))
    assert args.inputs[0].dataset_id == UUID(dataset)
    schema = refs.schema(AnalyzeInput.model_json_schema())
    assert '"format": "uuid"' not in json.dumps(schema)
    assert '"format": "uuid"' in json.dumps(AnalyzeInput.model_json_schema())
    policy = tool_decision(
        "run_sql", {"dataset_ids": [refs.resolve("dataset", "dataset_1")]}, set(), set()
    )
    assert policy.outcome == "reject" and policy.reason_code == "dataset_not_selected"


def test_citations_and_artifact_links_resolve_without_changing_declared_ids():
    evidence, artifact = [str(uuid4()) for _ in range(2)]
    refs = ModelReferences({"e1": evidence, "artifact_1": artifact})
    text = (
        "Revenue grew [e1].\n\n![Revenue](artifact:artifact_1)\n[artifact:artifact_1]"
    )
    answer = refs.answer(
        json.dumps(
            {"text": text, "evidence_ids": ["e1"], "artifact_ids": ["artifact_1"]}
        )
    )
    assert (
        answer.text
        == f"Revenue grew [evidence:{evidence}].\n\n![Revenue](artifact:{artifact})\n[artifact:{artifact}]"
    )
    assert answer.evidence_ids == [UUID(evidence)]
    assert answer.artifact_ids == [UUID(artifact)]
    assert refs.text_view(answer.text) == text
    for invalid in ("[e0]", "[e01]", "[e99]"):
        with pytest.raises(ValueError, match="Unknown evidence"):
            refs.answer(json.dumps({"text": invalid}))


def test_history_and_portability_keep_reference_identity():
    evidence, dataset, source = [str(uuid4()) for _ in range(3)]
    refs = ModelReferences({"e1": evidence, "dataset_4": dataset})
    history = [
        {
            "role": "assistant",
            "content": f"Result [evidence:{evidence}]",
            "references": {"evidence_ids": [evidence]},
            "selected_dataset_ids": [dataset],
            "source_versions": {source: 3},
        }
    ]
    model_history = refs.history(history)
    assert model_history[0]["content"] == "Result [e1]"
    assert model_history[0]["references"]["evidence_ids"] == ["e1"]
    assert model_history[0]["selected_dataset_ids"] == ["dataset_4"]
    assert model_history[0]["source_versions"] == {"source_1": 3}
    new_evidence = str(uuid4())
    imported = _remap_json(
        _safe_run_config({"reference_aliases": refs.aliases}), {evidence: new_evidence}
    )
    restored = ModelReferences(imported["reference_aliases"])
    assert restored.resolve("evidence", "e1") == new_evidence
    assert restored.reference("dataset", str(uuid4())) == "dataset_5"


async def test_file_sql_registers_short_tables_and_preserves_query_literals():
    dataset, source = [str(uuid4()) for _ in range(2)]
    refs = ModelReferences({"dataset_1": dataset})
    captured = []

    async def run_python(*args):
        captured.append(args[-1])
        return ToolResult(status="failed", summary="Scripted guest failure")

    runtime = SimpleNamespace(
        references=refs, settings=Settings(_env_file=None), run_python=run_python
    )
    tools = StructuredTools(runtime)
    sql = "SELECT 'dataset_1' AS label, COUNT(*) FROM dataset_1"
    await tools.query(
        SQLInput(sql=sql, dataset_ids=[UUID(dataset)]),
        str(uuid4()),
        SimpleNamespace(id=str(uuid4())),
        [SimpleNamespace(id=source, kind="csv")],
        [SimpleNamespace(id=dataset, source_id=source)],
        str(uuid4()),
    )
    config = captured[0].code.splitlines()[0]
    assert "'name': 'dataset_1'" in config
    assert sql in config
    assert captured[0].input_dataset_ids == [UUID(dataset)]
    with pytest.raises(SqlPolicyError):
        validate_sql(
            "SELECT * FROM dataset_2", dialect="duckdb", allowed_tables={"dataset_1"}
        )
    assert "'name': 'dataset_1'" in query_program(
        sql, [dataset], 5, 10, {dataset: "dataset_1"}
    )


async def test_loop_resolves_before_dispatch_and_recovers_unknown_references():
    dataset, evidence = [str(uuid4()) for _ in range(2)]
    refs = ModelReferences({"dataset_1": dataset})

    def response(name, args, index):
        return ModelResponse(
            finish_reason="tool_calls",
            tool_calls=[
                ModelToolCall(id=f"call-{index}", name=name, arguments=json.dumps(args))
            ],
        )

    responses = iter(
        [
            response("dataset_profile", {"dataset_id": "dataset_99"}, 1),
            response("dataset_profile", {"dataset_id": "dataset_1"}, 2),
            response("finish_answer", {"text": "42 [e999]", "evidence_ids": ["e1"]}, 3),
            response("finish_answer", {"text": "42 [e1]", "evidence_ids": ["e1"]}, 4),
        ]
    )
    requests = []

    class Model:
        async def complete(self, messages, schemas):
            requests.append(json.loads(json.dumps(messages)))
            assert '"format": "uuid"' not in json.dumps(schemas)
            return next(responses)

    dispatched, saved = [], []

    async def execute(call, args):
        dispatched.append(args.dataset_id)
        return ToolResult(status="ok", summary="42", evidence_ids=[evidence])

    async def validate(answer):
        assert answer.evidence_ids == [UUID(evidence)]

    async def save():
        saved.append(dict(refs.aliases))

    async def emit(*args):
        pass

    answer = await AgentLoop(
        Model(),
        Settings(_env_file=None),
        [Tool("dataset_profile", "Profile", DatasetInput, execute)],
        emit,
        validate,
        refs,
        save,
    ).run([], "en-IN")
    assert dispatched == [UUID(dataset)]
    assert answer.text == f"42 [evidence:{evidence}]"
    assert json.loads(requests[2][-1]["content"])["evidence_ids"] == ["e1"]
    assert "Unknown dataset reference" in requests[1][-1]["content"]
    assert "Unknown evidence reference" in requests[3][-1]["content"]
    assert saved[-1]["e1"] == evidence


def test_report_and_document_tools_resolve_all_reference_fields():
    from app.tools.reports import GenerateReportInput
    from app.tools.documents import SearchInput, PassageInput

    artifact, evidence, chunk = [str(uuid4()) for _ in range(3)]
    refs = ModelReferences({"artifact_1": artifact, "e1": evidence, "chunk_1": chunk})
    report = GenerateReportInput.model_validate(
        refs.arguments(
            json.dumps(
                {
                    "title": "Report",
                    "artifact_ids": ["artifact_1"],
                    "evidence_ids": ["e1"],
                    "code_artifact_ids": ["artifact_1"],
                    "input_artifact_ids": ["artifact_1"],
                }
            )
        )
    )
    assert report.code_artifact_ids == report.input_artifact_ids == [UUID(artifact)]
    assert report.evidence_ids == [UUID(evidence)]
    search = SearchInput.model_validate(
        refs.arguments(json.dumps({"query": "next", "hop_evidence_ids": ["e1"]}))
    )
    assert search.hop_evidence_ids == [UUID(evidence)]
    passage = PassageInput.model_validate(refs.arguments('{"chunk_id":"chunk_1"}'))
    assert passage.chunk_id == UUID(chunk)
