"""Validate real cases without downloads; cross-check local data when present."""

import csv
import hashlib
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from evaluation.cli import load_cases

EVALS = Path(__file__).resolve().parents[1]
PACK = EVALS / "fixtures/real-v1"
INVENTORY = EVALS / "cases/real-v1.json"


def test_real_inventory_is_valid_and_has_unreviewed_labels():
    cases = load_cases(INVENTORY, [], [])
    assert len(cases) >= 20
    assert all(case.review.provenance == "unreviewed" for case in cases)
    assert {case.answerability for case in cases} >= {
        "answerable",
        "ambiguous",
        "unsupported",
    }
    assert {case.language for case in cases} >= {"en-IN", "hi-IN"}
    assert any("survey" in case.tags for case in cases)
    assert any("retail" in case.tags for case in cases)
    for case in cases:
        assert case.sources
        assert "finish_answer" in case.expectations.allowed_actions
        for source in case.sources:
            assert source.sha256 and source.version
            # Every trial uploads a new source. The API starts it at version 1;
            # the evaluation pack's release name is a separate identity.
            assert source.version == "1"
        for passage in case.expectations.passages:
            assert passage.source_alias in {source.alias for source in case.sources}
            assert passage.contains.strip()
    code_switch = next(
        case for case in cases if case.id == "retail-codeswitch-top-country"
    )
    assert code_switch.language == "hi-Latn-IN"
    assert code_switch.answer_language == "hi-IN"
    rounded_share = next(
        calculation
        for case in cases
        if case.id == "retail-customer-null-en"
        for calculation in case.expectations.calculations
        if calculation.key == "missing_customer_percent"
    )
    assert rounded_share.value == Decimal("38.12")
    assert rounded_share.tolerance == Decimal("0.01")


def test_available_source_bytes_match_inventory():
    checked = set()
    for case in load_cases(INVENTORY, [], []):
        for source in case.sources:
            if source.path in checked:
                continue
            checked.add(source.path)
            path = EVALS / "fixtures" / source.path
            if not path.exists():
                pytest.skip("Download and generate real-v1 to verify local bytes")
            assert path.stat().st_size <= 25 * 1024 * 1024
            assert hashlib.sha256(path.read_bytes()).hexdigest() == source.sha256


def test_retail_gold_matches_independent_integer_sql():
    path = PACK / "derived/online-retail-2011-01.csv"
    if not path.exists():
        pytest.skip("Generate real-v1 to verify retail labels")
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    scale = 10 ** max(
        max(0, -Decimal(row["UnitPrice"]).as_tuple().exponent) for row in rows
    )
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE sales(invoice TEXT, qty INTEGER, price INTEGER, "
            "customer TEXT, country TEXT)"
        )
        connection.executemany(
            "INSERT INTO sales VALUES (?,?,?,?,?)",
            [
                (
                    row["InvoiceNo"],
                    int(row["Quantity"]),
                    int(Decimal(row["UnitPrice"]) * scale),
                    row["CustomerID"] or None,
                    row["Country"],
                )
                for row in rows
            ],
        )
        actual = connection.execute(
            "SELECT COUNT(*), SUM(qty*price), COUNT(DISTINCT invoice), "
            "COUNT(DISTINCT country), COUNT(DISTINCT customer), "
            "SUM(customer IS NULL), "
            "SUM(CASE WHEN customer IS NULL THEN qty*price ELSE 0 END) FROM sales "
            "WHERE qty > 0 AND price > 0 AND UPPER(invoice) NOT LIKE 'C%'"
        ).fetchone()
    finally:
        connection.close()
    expected = {
        "qualifying_rows": actual[0],
        "sales_gbp": Decimal(actual[1]) / scale,
        "invoice_count": actual[2],
        "country_count": actual[3],
        "customer_count": actual[4],
        "missing_customer_lines": actual[5],
        "missing_customer_sales_gbp": Decimal(actual[6]) / scale,
        "missing_customer_sales_percent": (
            Decimal(actual[6]) * 100 / actual[1]
        ).quantize(Decimal("0.01")),
    }
    # Compare the global aggregates only. A country-filtered case deliberately
    # reuses sales_gbp for a different value.
    baseline_cases = {
        "retail-jan-sales-en",
        "retail-distinct-countries",
        "retail-distinct-customer-count",
        "retail-customer-null-en",
        "retail-missing-customer-sales",
    }
    calculations = {
        calculation.key: calculation.value
        for case in load_cases(INVENTORY, [], [])
        if case.id in baseline_cases
        for calculation in case.expectations.calculations
    }
    for key, value in expected.items():
        assert calculations[key] == Decimal(value)


def test_real_reference_answers_and_artifact_gold_are_complete():
    cases = {case.id: case for case in load_cases(INVENTORY, [], [])}
    for case in cases.values():
        assert case.expectations.rubric["review_criteria"].keys() >= {
            "complete",
            "partial",
            "failed",
            "separate_checks",
        }
        assert "register_dataset" not in case.expectations.allowed_actions
        assert "generate_report" not in case.expectations.allowed_actions
        if "survey" in case.tags:
            assert case.expectations.rubric["reference_answer"]["required_claims"]
            assert all(
                p.match_policy == "diagnostic" for p in case.expectations.passages
            )
    for ident in ["retail-sales-chart-csv", "retail-export-and-chart-hi"]:
        rubric = cases[ident].expectations.rubric
        assert rubric["expected_csv_rows"] == 22
        assert len(rubric["expected_country_sales"]) == 22
        assert sum(
            Decimal(v) for v in rubric["expected_country_sales"].values()
        ) == Decimal("691364.56")
        assert [row[0] for row in rubric["expected_ordered_country_sales"]] == [
            "United Kingdom",
            "Netherlands",
            "EIRE",
            "France",
            "Germany",
        ]
    share = next(
        x
        for x in cases["retail-missing-customer-sales"].expectations.calculations
        if x.key == "missing_customer_sales_percent"
    )
    assert share.value == Decimal("17.63")
    assert share.tolerance == Decimal("0.01")
    assert "cost data" not in cases["retail-missing-cost-unsupported"].question
    assert "If this source" not in cases["retail-scope-clarification"].question
    assert "FY23/FY24" in cases["survey-inflation-drivers-en"].question
