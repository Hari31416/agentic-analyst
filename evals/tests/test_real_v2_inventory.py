"""Validate the 50-case pack and independently check new numeric gold."""

import ast
from copy import deepcopy
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import runpy
import sqlite3

import pytest

from evaluation.cli import load_cases

EVALS = Path(__file__).resolve().parents[1]
INVENTORY = EVALS / "cases/real-v2.json"
BASE = EVALS / "cases/real-v1.json"
GENERATOR = EVALS / "generators/generate_real_v2.py"


def test_real_v2_retains_exact_v1_and_has_twenty_distinct_additions():
    original = json.loads(BASE.read_text())["cases"]
    current = json.loads(INVENTORY.read_text())["cases"]
    assert len(current) == 50
    assert current[:30] == original
    assert len({c["id"] for c in current}) == 50
    assert len({c["question"] for c in current}) == 50
    assert all("real-v2-added" in c["tags"] for c in current[30:])
    assert [c.id for c in load_cases(INVENTORY, [], ["real-v2-added"])] == [
        c["id"] for c in current[30:]
    ]
    assert sum("retail" in c["tags"] for c in current) == 32
    assert sum("survey" in c["tags"] for c in current) == 18


def test_real_v2_sources_tool_names_and_review_contracts():
    known = {
        s["path"]: s
        for c in json.loads(BASE.read_text())["cases"]
        for s in c["sources"]
    }
    runtime = ast.parse((EVALS.parent / "backend/app/agent/runtime.py").read_text())
    tools = {
        node.args[0].value
        for node in ast.walk(runtime)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"Tool", "structured_tool"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    } | {"finish_answer"}
    for case in load_cases(INVENTORY, [], ["real-v2-added"]):
        assert case.review.provenance == "unreviewed"
        assert case.expectations.rubric["manual_review"]
        assert case.expectations.rubric["allowed_actions_rationale"]
        assert case.expectations.rubric["review_criteria"].keys() >= {
            "complete",
            "partial",
            "failed",
            "separate_checks",
        }
        assert set(case.expectations.allowed_actions) <= tools
        assert "finish_answer" in case.expectations.allowed_actions
        assert len(case.expectations.allowed_actions) == len(
            set(case.expectations.allowed_actions)
        )
        for source in case.sources:
            assert source.sha256 == known[source.path]["sha256"]
            assert source.version == "1"
        if "survey" in case.tags:
            assert case.expectations.rubric["reference_answer"]["required_claims"]
            assert case.expectations.rubric["reference_answer"]["locations"]
            assert all(
                p.match_policy == "diagnostic" for p in case.expectations.passages
            )
        for calculation in case.expectations.calculations:
            assert calculation.key in case.question
            assert calculation.tolerance <= Decimal("0.01")


@pytest.fixture
def retail_sql():
    path = EVALS / "fixtures/real-v1/derived/online-retail-2011-01.csv"
    if not path.exists():
        pytest.skip("Pinned real-v1 inputs are required for numeric checks")
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    manifest = json.loads((EVALS / "fixtures/real-v1/manifest.json").read_text())
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == manifest["retail_slice"]["sha256"]
    )
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE r(invoice TEXT, stock TEXT, description TEXT, qty INTEGER, cents INTEGER, date TEXT, customer TEXT, country TEXT)"
    )
    assert all(
        Decimal(r["UnitPrice"]) * 100
        == (Decimal(r["UnitPrice"]) * 100).to_integral_value()
        for r in rows
    )
    connection.executemany(
        "INSERT INTO r VALUES (?,?,?,?,?,?,?,?)",
        [
            (
                r["InvoiceNo"],
                r["StockCode"],
                r["Description"],
                int(r["Quantity"]),
                int(Decimal(r["UnitPrice"]) * 100),
                r["InvoiceDate"],
                r["CustomerID"] or None,
                r["Country"],
            )
            for r in rows
        ],
    )
    connection.execute(
        "CREATE VIEW q AS SELECT * FROM r WHERE UPPER(invoice) NOT LIKE 'C%' AND qty>0 AND cents>0"
    )
    yield connection, rows
    connection.close()


def test_real_v2_manifest_matches_case_and_base_hashes():
    manifest = json.loads((EVALS / "fixtures/real-v2/manifest.json").read_text())
    assert manifest["version"] == "real-v2"
    assert (
        manifest["case_inventory"]["sha256"]
        == hashlib.sha256(INVENTORY.read_bytes()).hexdigest()
    )
    assert (
        manifest["base_inventory"]["sha256"]
        == hashlib.sha256(BASE.read_bytes()).hexdigest()
    )
    assert manifest["coverage"]["total_cases"] == 50
    assert manifest["coverage"]["added_cases"] == 20
    assert manifest["review"]["provenance"] == "unreviewed"


def test_generator_rejects_duplicate_or_changed_base_inventory(tmp_path):
    generator = runpy.run_path(str(GENERATOR))
    original = json.loads(BASE.read_text())["cases"]
    additions = deepcopy(json.loads(INVENTORY.read_text())["cases"][30:])
    additions[0]["id"] = original[0]["id"]
    with pytest.raises(ValueError, match="unique"):
        generator["validate_additions"](original, additions)
    changed = tmp_path / "base.json"
    changed.write_text(BASE.read_text() + " ")
    load_inputs = generator["load_inputs"]
    load_inputs.__globals__["BASE_INVENTORY"] = changed
    with pytest.raises(ValueError, match="Frozen"):
        load_inputs()


def test_all_new_retail_values_against_independent_integer_sql(retail_sql):
    db, _ = retail_sql
    expected = {
        e["key"]: Decimal(e["value"])
        for c in json.loads(INVENTORY.read_text())["cases"][30:]
        for e in c["expectations"]["calculations"]
    }
    checked = {}

    def scalar(key, expression, table="q", where="1"):
        checked[key] = Decimal(
            str(
                db.execute(
                    f"SELECT {expression} FROM {table} WHERE {where}"
                ).fetchone()[0]
            )
        )

    for prefix, predicate in [
        ("weekend", "strftime('%w',date) IN ('0','6')"),
        ("weekday", "strftime('%w',date) NOT IN ('0','6')"),
    ]:
        for suffix, expression in [
            ("lines", "COUNT(*)"),
            ("invoices", "COUNT(DISTINCT invoice)"),
            ("sales_gbp", "SUM(qty*cents)/100.0"),
            ("avg_line_gbp", "SUM(qty*cents)/100.0/COUNT(*)"),
        ]:
            scalar(f"{prefix}_{suffix}", expression, where=predicate)
    for prefix, low, high in [
        ("days_01_07", 1, 7),
        ("days_08_14", 8, 14),
        ("days_15_21", 15, 21),
        ("days_22_31", 22, 31),
    ]:
        predicate = f"CAST(strftime('%d',date) AS INTEGER) BETWEEN {low} AND {high}"
        scalar(prefix + "_lines", "COUNT(*)", where=predicate)
        scalar(prefix + "_sales_gbp", "SUM(qty*cents)/100.0", where=predicate)
    endpoints = db.execute(
        "SELECT MIN(substr(date,1,10)),MAX(substr(date,1,10)) FROM q"
    ).fetchone()
    assert endpoints == ("2011-01-04", "2011-01-31")
    for prefix, day in zip(["first_day", "last_day"], endpoints):
        scalar(prefix + "_lines", "COUNT(*)", where=f"substr(date,1,10)='{day}'")
        scalar(
            prefix + "_sales_gbp",
            "SUM(qty*cents)/100.0",
            where=f"substr(date,1,10)='{day}'",
        )
    for prefix, cancellation in [
        ("cancel", "LIKE 'C%'"),
        ("noncancel", "NOT LIKE 'C%'"),
    ]:
        for sign, comparison in [("negative", "<0"), ("nonnegative", ">=0")]:
            scalar(
                f"{prefix}_{sign}_quantity_rows",
                "COUNT(*)",
                "r",
                f"UPPER(invoice) {cancellation} AND qty{comparison}",
            )
    for key, expr, table, where in [
        ("raw_rows", "COUNT(*)", "r", "1"),
        ("raw_signed_row_amount_gbp", "SUM(qty*cents)/100.0", "r", "1"),
        ("raw_negative_quantity_rows", "COUNT(*)", "r", "qty<0"),
        (
            "raw_negative_quantity_signed_amount_gbp",
            "SUM(qty*cents)/100.0",
            "r",
            "qty<0",
        ),
        ("qualifying_rows", "COUNT(*)", "q", "1"),
        ("qualifying_sales_gbp", "SUM(qty*cents)/100.0", "q", "1"),
        ("average_qualifying_line_gbp", "SUM(qty*cents)/100.0/COUNT(*)", "q", "1"),
        (
            "average_qualifying_invoice_gbp",
            "SUM(qty*cents)/100.0/COUNT(DISTINCT invoice)",
            "q",
            "1",
        ),
    ]:
        scalar(key, expr, table, where)
    db.execute(
        "CREATE VIEW invoices AS SELECT invoice,COUNT(*) AS lines,SUM(qty*cents) AS sales FROM q GROUP BY invoice"
    )
    for prefix, predicate in [("single_line", "lines=1"), ("multi_line", "lines>=2")]:
        scalar(prefix + "_invoice_count", "COUNT(*)", "invoices", predicate)
        scalar(prefix + "_qualifying_lines", "SUM(lines)", "invoices", predicate)
        scalar(prefix + "_sales_gbp", "SUM(sales)/100.0", "invoices", predicate)
    leaders = db.execute(
        "SELECT country,SUM(qty*cents) FROM q GROUP BY country ORDER BY SUM(qty*cents) DESC,country LIMIT 3"
    ).fetchall()
    assert [r[0] for r in leaders] == ["United Kingdom", "Netherlands", "EIRE"]
    checked["top_three_countries_sales_gbp"] = Decimal(sum(r[1] for r in leaders)) / 100
    total = db.execute("SELECT SUM(qty*cents) FROM q").fetchone()[0]
    checked["top_three_sales_share_percent"] = (
        Decimal(sum(r[1] for r in leaders)) * 100 / total
    )
    stock = db.execute(
        "SELECT stock,SUM(qty*cents),SUM(qty),COUNT(*) FROM q GROUP BY stock ORDER BY SUM(qty*cents) DESC,stock LIMIT 1"
    ).fetchone()
    assert stock[0] == "23166"
    checked.update(
        leading_stock_sales_gbp=Decimal(stock[1]) / 100,
        leading_stock_qualifying_units=Decimal(stock[2]),
        leading_stock_qualifying_lines=Decimal(stock[3]),
    )
    for prefix, predicate, units in [
        ("unit_price_under_1", "cents<100", False),
        ("unit_price_1_or_more", "cents>=100", False),
        ("quantity_under_100", "qty<100", True),
        ("quantity_100_or_more", "qty>=100", True),
    ]:
        scalar(prefix + "_lines", "COUNT(*)", where=predicate)
        scalar(prefix + "_sales_gbp", "SUM(qty*cents)/100.0", where=predicate)
        scalar(
            prefix + "_sales_share_percent",
            f"SUM(qty*cents)*100.0/{total}",
            where=predicate,
        )
        if units:
            scalar(prefix + "_units", "SUM(qty)", where=predicate)
    for prefix, predicate in [
        ("uk", "country='United Kingdom'"),
        ("other_countries", "country!='United Kingdom'"),
    ]:
        scalar(prefix + "_qualifying_lines", "COUNT(*)", where=predicate)
        scalar(
            prefix + "_missing_customer_lines", "SUM(customer IS NULL)", where=predicate
        )
        scalar(
            prefix + "_missing_customer_line_share_percent",
            "SUM(customer IS NULL)*100.0/COUNT(*)",
            where=predicate,
        )
        scalar(
            prefix + "_missing_customer_sales_gbp",
            "SUM(CASE WHEN customer IS NULL THEN qty*cents ELSE 0 END)/100.0",
            where=predicate,
        )
        scalar(
            prefix + "_missing_customer_sales_share_percent",
            "SUM(CASE WHEN customer IS NULL THEN qty*cents ELSE 0 END)*100.0/SUM(qty*cents)",
            where=predicate,
        )
    assert len(expected) == 67
    assert checked.keys() == expected.keys()
    for key, value in expected.items():
        assert abs(checked[key] - value) <= Decimal("0.005"), key


def test_generator_reproduces_frozen_pack_and_verifies_reference_pages():
    generator = runpy.run_path(str(GENERATOR))
    inherited, rows = generator["load_inputs"]()
    gold = generator["additional_gold"](rows)
    additions = generator["additional_cases"](
        gold, generator["source_catalog"](inherited)
    )
    assert (
        generator["validate_additions"](inherited, additions)
        == json.loads(INVENTORY.read_text())["cases"]
    )
    assert all(len(tag) > 1 for c in additions for tag in c["tags"])
    broken = deepcopy(additions)
    broken[-1]["expectations"]["rubric"]["reference_answer"]["locations"]["inflation"][
        "pdf_pages"
    ] = [14]
    with pytest.raises(ValueError, match="Unverified passage"):
        generator["validate_additions"](inherited, broken)
