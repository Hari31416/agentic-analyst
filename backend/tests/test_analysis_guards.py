import csv
from decimal import Decimal

import pytest

import app.tools.analysis_engine as engine
from app.tools.analysis_engine import (
    AnalysisError,
    _decimal_operation,
    apply_operation,
    cell,
    number,
    run_analysis,
)


def test_empty_frames_validate_schema_and_global_aggregate_semantics():
    frames = {"data": []}
    units = {"data": {"amount": "INR"}}
    schemas = {"data": ["amount"]}
    with pytest.raises(AnalysisError, match="unknown_column"):
        apply_operation(
            frames,
            units,
            {"kind": "filter", "frame": "data", "columns": ["missing"]},
            schemas,
        )

    note = apply_operation(
        frames,
        units,
        {
            "kind": "aggregate",
            "frame": "data",
            "columns": [],
            "metrics": [
                {"column": "amount", "function": "count", "output": "count"},
                {"column": "amount", "function": "sum", "output": "total"},
                {"column": "amount", "function": "mean", "output": "average"},
                {"column": "amount", "function": "min", "output": "minimum"},
                {"column": "amount", "function": "max", "output": "maximum"},
            ],
        },
        schemas,
    )
    assert frames["data"] == [
        {
            "count": 0,
            "total": Decimal(0),
            "average": None,
            "minimum": None,
            "maximum": None,
        }
    ]
    assert schemas["data"] == ["count", "total", "average", "minimum", "maximum"]
    assert units["data"] == {
        "total": "INR",
        "average": "INR",
        "minimum": "INR",
        "maximum": "INR",
    }
    assert note["output_rows"] == 1


def test_empty_grouped_aggregate_and_join_keep_transformed_headers(tmp_path):
    inputs = tmp_path / "inputs"
    outputs = tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "empty.csv").write_text("region,amount\n", encoding="utf-8")
    config = {
        "inputs": [{"alias": "data", "path": "empty.csv", "units": {"amount": "INR"}}],
        "operations": [
            {
                "kind": "aggregate",
                "frame": "data",
                "columns": ["region"],
                "metrics": [{"column": "amount", "function": "sum", "output": "total"}],
            }
        ],
        "result_frame": "data",
        "max_rows": 10,
        "assumptions": [],
        "chart": None,
    }
    result = run_analysis(config, input_root=inputs, output_root=outputs)
    with (outputs / "result.csv").open(newline="", encoding="utf-8") as stream:
        assert next(csv.reader(stream)) == ["region", "total"]
    assert result["status"] == "ok" and result["row_count"] == 0
    assert result["units"] == {"total": "INR"}

    frames = {"left": [], "right": []}
    units = {"left": {}, "right": {}}
    schemas = {"left": ["id"], "right": ["key", "label"]}
    with pytest.raises(AnalysisError, match="unknown_column"):
        apply_operation(
            frames,
            units,
            {
                "kind": "join",
                "frame": "left",
                "columns": ["absent"],
                "right_frame": "right",
                "right_on": ["key"],
                "relationship": "many_to_one",
                "how": "left",
            },
            schemas,
        )
    frames = {"left": [], "right": []}
    units = {"left": {}, "right": {}}
    schemas = {"left": ["id", "label"], "right": ["key", "label"]}
    apply_operation(
        frames,
        units,
        {
            "kind": "join",
            "frame": "left",
            "columns": ["id"],
            "right_frame": "right",
            "right_on": ["key"],
            "relationship": "many_to_one",
            "how": "left",
        },
        schemas,
    )
    assert schemas["left"] == ["id", "label", "key", "label_right"]


def test_decimal_operations_are_exact_or_rejected():
    operand = number("9" * 60)
    product = _decimal_operation("multiply", operand, operand)
    expected = Decimal(int("9" * 60) ** 2)
    assert product == expected
    assert len(product.as_tuple().digits) == 120
    with pytest.raises(AnalysisError, match="nonterminating_decimal_result"):
        _decimal_operation("divide", Decimal(1), Decimal(3))
    with pytest.raises(AnalysisError, match="numeric_precision_limit"):
        number("9" * 61)

    frames = {"data": [{"a": "9" * 60, "b": "9" * 60}]}
    units = {"data": {}}
    schemas = {"data": ["a", "b"]}
    apply_operation(
        frames,
        units,
        {
            "kind": "convert",
            "frame": "data",
            "columns": ["a", "b"],
            "data_type": "number",
        },
        schemas,
    )
    apply_operation(
        frames,
        units,
        {
            "kind": "derive",
            "frame": "data",
            "columns": ["a", "b"],
            "output": "product",
            "function": "multiply",
        },
        schemas,
    )
    assert cell(frames["data"][0]["product"]) == str(int("9" * 60) ** 2)


@pytest.mark.parametrize("kind", ["aggregate", "melt"])
def test_reshape_cell_limits_reject_before_building_outputs(monkeypatch, kind):
    monkeypatch.setattr(engine, "MAX_CELLS", 20)
    if kind == "aggregate":
        frames = {"data": [{"id": str(i), "amount": "1"} for i in range(10)]}
        units = {"data": {}}
        schemas = {"data": ["id", "amount"]}
        operation = {
            "kind": "aggregate",
            "frame": "data",
            "columns": ["id"],
            "metrics": [
                {"column": "amount", "function": "sum", "output": "sum_a"},
                {"column": "amount", "function": "sum", "output": "sum_b"},
            ],
        }
    else:
        frames = {"data": [{"id": str(i), "a": "1", "b": "2"} for i in range(5)]}
        units = {"data": {}}
        schemas = {"data": ["id", "a", "b"]}
        operation = {
            "kind": "melt",
            "frame": "data",
            "columns": ["id"],
            "value_columns": ["a", "b"],
        }
    with pytest.raises(AnalysisError, match="expansion_limit"):
        apply_operation(frames, units, operation, schemas)


def test_melt_and_pivot_replace_units_with_output_columns_only():
    frames = {
        "data": [
            {"id": "A", "Jan": "10", "Feb": "20"},
            {"id": "B", "Jan": "30", "Feb": "40"},
        ]
    }
    units = {"data": {"id": "identifier", "Jan": "INR", "Feb": "INR"}}
    schemas = {"data": ["id", "Jan", "Feb"]}
    apply_operation(
        frames,
        units,
        {
            "kind": "melt",
            "frame": "data",
            "columns": ["id"],
            "value_columns": ["Jan", "Feb"],
        },
        schemas,
    )
    assert units["data"] == {"id": "identifier", "value": "INR"}
    apply_operation(
        frames,
        units,
        {
            "kind": "pivot",
            "frame": "data",
            "columns": ["id"],
            "value_columns": ["variable", "value"],
        },
        schemas,
    )
    assert units["data"] == {"id": "identifier", "Feb": "INR", "Jan": "INR"}
    assert schemas["data"] == ["id", "Feb", "Jan"]


def test_derive_cannot_relabel_known_units():
    frames = {"data": [{"usd": "2", "inr": "3"}]}
    units = {"data": {"usd": "USD", "inr": "INR"}}
    schemas = {"data": ["usd", "inr"]}
    with pytest.raises(AnalysisError, match="derived_unit_mismatch"):
        apply_operation(
            frames,
            units,
            {
                "kind": "derive",
                "frame": "data",
                "columns": ["usd", "inr"],
                "output": "total",
                "function": "add",
                "unit": "USD",
            },
            schemas,
        )

    frames = {"data": [{"usd": "2", "inr": "3"}]}
    units = {"data": {"usd": "USD", "inr": "INR"}}
    schemas = {"data": ["usd", "inr"]}
    with pytest.raises(AnalysisError, match="derived_unit_mismatch"):
        apply_operation(
            frames,
            units,
            {
                "kind": "derive",
                "frame": "data",
                "columns": ["usd", "inr"],
                "output": "total",
                "function": "multiply",
                "unit": "EUR",
            },
            schemas,
        )
