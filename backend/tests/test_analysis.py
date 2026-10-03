"""Deterministic tests of application-owned algorithms, never model-authored code."""

import copy
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.tools.analysis import AnalyzeInput, Operation
from app.tools.analysis_engine import AnalysisError, apply_operation, cell, number
from app.sources.files import FileIngestionError, profile_upload, _iter_dataset_rows


def execute(rows, spec, units=None, right=None, right_units=None):
    operation = Operation(frame="data", **spec).model_dump(mode="json")
    frames = {"data": copy.deepcopy(rows), "lookup": copy.deepcopy(right or [])}
    metadata = apply_operation(
        frames, {"data": units or {}, "lookup": right_units or {}}, operation
    )
    return frames["data"], metadata


def test_exact_decimal_and_identifier_preservation():
    rows, _ = execute(
        [
            {"id": "001", "amount": "9007199254740993.01"},
            {"id": "002", "amount": "0.02"},
        ],
        {"kind": "convert", "columns": ["amount"], "data_type": "number"},
    )
    result, metadata = execute(
        rows,
        {
            "kind": "aggregate",
            "metrics": [{"column": "amount", "function": "sum", "output": "total"}],
        },
    )
    assert result == [{"total": Decimal("9007199254740993.03")}]
    assert rows[0]["id"] == "001"
    assert cell(result[0]["total"]) == "9007199254740993.03"
    assert cell(9007199254740993) == "9007199254740993"
    assert cell(float("inf")) is None
    for value in ["NaN", "Infinity", "1e100"]:
        with pytest.raises(AnalysisError):
            number(value)


def test_cleaning_is_explicit_and_records_removed_rows():
    rows = [
        {"id": "a", "amount": ""},
        {"id": "a", "amount": "10"},
        {"id": "b", "amount": "20"},
    ]
    with pytest.raises(AnalysisError, match="missing_values_require_policy"):
        execute(rows, {"kind": "missing", "columns": ["amount"]})
    clean, note = execute(
        rows, {"kind": "missing", "columns": ["amount"], "policy": "drop"}
    )
    assert note["missing_counts"] == {"amount": 1} and note["rows_removed"] == 1
    with pytest.raises(AnalysisError, match="duplicate_identifiers"):
        execute(rows, {"kind": "duplicates", "columns": ["id"]})
    unique, note = execute(
        rows, {"kind": "duplicates", "columns": ["id"], "policy": "drop"}
    )
    assert len(unique) == 2 and note["duplicate_rows"] == 1
    with pytest.raises(AnalysisError, match="aggregate_missing"):
        execute(
            rows,
            {
                "kind": "aggregate",
                "metrics": [{"column": "amount", "function": "sum", "output": "total"}],
            },
        )
    filled, note = execute(
        rows, {"kind": "missing", "columns": ["amount"], "policy": "fill", "value": "0"}
    )
    assert filled[0]["amount"] == "0" and note["policy"] == "fill"
    filtered, note = execute(
        clean, {"kind": "filter", "columns": ["id"], "comparison": "eq", "value": "b"}
    )
    assert len(filtered) == 1 and note["rows_removed"] == 1


def test_join_cardinality_row_loss_units_and_key_mapping():
    spec = {
        "kind": "join",
        "columns": ["id"],
        "right_frame": "lookup",
        "right_on": ["key"],
    }
    rows = [{"id": "001", "amount": "10"}, {"id": "002", "amount": "20"}]
    right = [{"key": "001", "label": "A"}, {"key": "003", "label": "C"}]
    joined, note = execute(rows, spec, right=right)
    assert len(joined) == 2 and joined[1]["label"] is None
    assert (
        note["unmatched_left"] == 1 and note["unmatched_right"] == 1 and note["warning"]
    )
    with pytest.raises(AnalysisError, match="right_keys_not_unique"):
        execute(rows, spec, right=right + [right[0]])
    joined, note = execute(
        rows + [rows[0]],
        {**spec, "relationship": "many_to_many"},
        right=right + [right[0]],
    )
    assert len(joined) == 5 and "multiply" in note["warning"]
    with pytest.raises(AnalysisError, match="left_keys_not_unique"):
        execute(rows + [rows[0]], {**spec, "relationship": "one_to_one"}, right=right)
    with pytest.raises(AnalysisError, match="join_key_types_require_mapping"):
        execute(rows, spec, right=[{"key": 1}])
    with pytest.raises(AnalysisError, match="join_null_keys"):
        execute(rows, spec, right=[{"key": ""}])
    with pytest.raises(AnalysisError, match="join_key_unit_mismatch"):
        execute(
            rows, spec, units={"id": "USD"}, right=right, right_units={"key": "INR"}
        )


def test_units_date_fiscal_and_reshape():
    rows = [
        {"a": "1", "b": "2", "date": "2026-03-31"},
        {"a": "3", "b": "4", "date": "2026-04-01"},
    ]
    with pytest.raises(AnalysisError, match="derived_unit_mismatch"):
        execute(
            rows,
            {
                "kind": "derive",
                "columns": ["a", "b"],
                "output": "sum",
                "function": "add",
            },
            units={"a": "INR", "b": "USD"},
        )
    dated, _ = execute(
        rows, {"kind": "convert", "columns": ["date"], "data_type": "date"}
    )
    dated, note = execute(
        dated,
        {
            "kind": "derive",
            "columns": ["date"],
            "output": "fy",
            "function": "fiscal_year",
        },
    )
    assert [r["fy"] for r in dated] == [2025, 2026] and note[
        "fiscal_label"
    ] == "start year"
    melted, _ = execute(
        rows, {"kind": "melt", "columns": ["date"], "value_columns": ["a", "b"]}
    )
    assert len(melted) == 4
    restored, _ = execute(
        melted,
        {"kind": "pivot", "columns": ["date"], "value_columns": ["variable", "value"]},
    )
    assert restored == rows
    with pytest.raises(AnalysisError, match="pivot_duplicate"):
        execute(
            melted + [melted[0]],
            {
                "kind": "pivot",
                "columns": ["date"],
                "value_columns": ["variable", "value"],
            },
        )


def test_statistics_correlation_regression_and_outliers():
    rows = [{"y": str(1 + 2 * i), "x": str(i)} for i in range(1, 6)]
    _, note = execute(
        rows, {"kind": "statistics", "columns": ["y", "x"], "function": "regression"}
    )
    assert note["regression"]["intercept"] == pytest.approx(1)
    assert note["regression"]["coefficients"] == pytest.approx([2])
    assert note["regression"]["r_squared"] == pytest.approx(1)
    assert note["sample_size"] == 5 and note["causal_claim"] is False
    _, note = execute(
        rows, {"kind": "statistics", "columns": ["y", "x"], "function": "correlation"}
    )
    assert note["correlation"][0][1] == pytest.approx(1)
    _, note = execute(
        rows, {"kind": "statistics", "columns": ["x"], "function": "describe"}
    )
    assert note["statistics"]["x"]["mean"] == 3
    _, note = execute(
        [{"x": v} for v in ["1", "2", "2", "3", "100"]],
        {"kind": "statistics", "columns": ["x"], "function": "outliers"},
    )
    assert note["outliers"]["count"] == 1
    with pytest.raises(AnalysisError, match="rank_deficient"):
        execute(
            [{"y": "2", "x": "1"}] * 4,
            {"kind": "statistics", "columns": ["y", "x"], "function": "regression"},
        )


def test_input_contract_rejects_ambiguous_operation():
    with pytest.raises(ValidationError):
        AnalyzeInput(
            inputs=[{"alias": "data", "dataset_id": uuid4(), "artifact_id": uuid4()}],
            result_frame="data",
        )
    for op in [
        {"kind": "filter", "columns": ["a"], "value": 1},
        {"kind": "join", "columns": ["id"]},
        {"kind": "missing", "columns": ["a"], "policy": "fill"},
        {"kind": "derive", "columns": ["a"], "function": "add", "output": "b"},
        {"kind": "filter", "columns": ["a"], "value": float("nan")},
        {"kind": "duplicates", "columns": ["a"], "policy": "fill"},
    ]:
        with pytest.raises(ValidationError):
            Operation(frame="data", **op)


def test_json_adapter_preserves_precision_and_rejects_nested_duplicates():
    data = b'[{"id":"001","amount":9007199254740993.01},{"id":"002","amount":0.02}]'
    profile = profile_upload("records.json", data)[0]
    assert profile["details"]["row_count"] == 2
    rows = list(_iter_dataset_rows("records.json", data, "data"))
    assert rows == [["001", "9007199254740993.01"], ["002", "0.02"]]
    for content in [b'[{"a":1,"a":2}]', b'[{"a":{"x":1}}]', b'[{"a":NaN}]', b'{"a":1}']:
        with pytest.raises(FileIngestionError, match="Flat record"):
            profile_upload("records.json", content)


def test_parquet_adapter_preserves_decimal_dates_and_identifiers():
    import io
    from datetime import date
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "id": ["001", "002"],
            "amount": pa.array(
                [Decimal("123.45"), Decimal("0.02")], type=pa.decimal128(10, 2)
            ),
            "date": [date(2026, 1, 1), date(2026, 1, 2)],
        }
    )
    out = io.BytesIO()
    pq.write_table(table, out)
    data = out.getvalue()
    assert profile_upload("records.parquet", data)[0]["details"]["row_count"] == 2
    assert list(_iter_dataset_rows("records.parquet", data, "data"))[0] == [
        "001",
        "123.45",
        "2026-01-01",
    ]
    bad = io.BytesIO()
    pq.write_table(pa.table({"nested": [[1, 2]]}), bad)
    with pytest.raises(FileIngestionError):
        profile_upload("records.parquet", bad.getvalue())
