"""Generate the 50-case real-v2 pack from frozen v1 and reviewed additions.

Uses the pinned real-v1 sources without writing to v1. Run from backend with
``uv run python ../evals/generators/generate_real_v2.py``. No network/model calls.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import csv
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "real_v1_generator", ROOT / "evals/generators/generate_real_v1.py"
)
assert _spec and _spec.loader
v1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v1)
BASE_INVENTORY = ROOT / "evals/cases/real-v1.json"
BASE_SHA256 = "b1e17fc5234b706351c9832e6359ec3af9d34a505ea219b7f1dffc791f0d7192"
OUTPUT = ROOT / "evals/cases/real-v2.json"
MANIFEST = ROOT / "evals/fixtures/real-v2/manifest.json"


def load_inputs() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Fail closed rather than silently changing the inherited 30 questions."""
    if v1.sha256(BASE_INVENTORY) != BASE_SHA256:
        raise ValueError(
            "Frozen real-v1 inventory changed; review before rebuilding v2"
        )
    inherited = json.loads(BASE_INVENTORY.read_text(encoding="utf-8"))["cases"]
    if len(inherited) != 30:
        raise ValueError("Expected exactly 30 inherited real-v1 cases")
    v1.verify_download_manifest()
    verified: set[str] = set()
    for case in inherited:
        for source in case["sources"]:
            if source["path"] not in verified:
                if v1.sha256(v1.FIXTURE_ROOT / source["path"]) != source["sha256"]:
                    raise ValueError(f"Pinned source changed: {source['path']}")
                verified.add(source["path"])
    with v1.MONTH_CSV.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    return inherited, rows


def source_catalog(inherited: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {s["alias"]: deepcopy(s) for c in inherited for s in c["sources"]}


def validate_additions(
    inherited: list[dict[str, Any]], additions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if len(additions) != 20:
        raise ValueError("V2 requires exactly 20 additions")
    cases = deepcopy(inherited) + additions
    ids = [c["id"] for c in cases]
    if len(ids) != 50 or len(set(ids)) != 50:
        raise ValueError("V2 case IDs must be unique across all 50 questions")
    known_sources = {s["path"]: s for c in inherited for s in c["sources"]}
    readers: dict[str, PdfReader] = {}
    page_texts: dict[tuple[str, int], str] = {}
    for case in additions:
        if "real-v2-added" not in case["tags"]:
            raise ValueError(f"Missing new-case cohort tag: {case['id']}")
        if case["review"]["provenance"] != "unreviewed":
            raise ValueError("Agent-reviewed gold must not claim human provenance")
        for source in case["sources"]:
            expected = known_sources.get(source["path"])
            if (
                not expected
                or source["sha256"] != expected["sha256"]
                or source["version"] != "1"
            ):
                raise ValueError(f"Unpinned source in {case['id']}")
        for passage in case["expectations"]["passages"]:
            source = next(
                s for s in case["sources"] if s["alias"] == passage["source_alias"]
            )
            if source["kind"] == "pdf":
                if source["path"] not in readers:
                    readers[source["path"]] = PdfReader(
                        v1.FIXTURE_ROOT / source["path"]
                    )
                locations = case["expectations"]["rubric"]["reference_answer"][
                    "locations"
                ]
                page_numbers = locations[source["alias"]]["pdf_pages"]
                pages = []
                for number in page_numbers:
                    if not 1 <= number <= len(readers[source["path"]].pages):
                        raise ValueError(f"Invalid reference page in {case['id']}")
                    key = (source["path"], number)
                    if key not in page_texts:
                        page_texts[key] = v1.normalized(
                            readers[source["path"]].pages[number - 1].extract_text()
                            or ""
                        )
                    pages.append(page_texts[key])
            else:
                pages = [v1.normalized((v1.FIXTURE_ROOT / source["path"]).read_text())]
            for phrase in [passage["contains"], *passage.get("contains_any", [])]:
                if not any(v1.normalized(phrase) in page for page in pages):
                    raise ValueError(f"Unverified passage in {case['id']}: {phrase}")
    return cases


CENT = Decimal("0.01")
HUNDRED = Decimal("100")


def amount(row: dict[str, str]) -> Decimal:
    return Decimal(row["Quantity"]) * Decimal(row["UnitPrice"])


def qualifies(row: dict[str, str]) -> bool:
    return (
        not row["InvoiceNo"].upper().startswith("C")
        and int(row["Quantity"]) > 0
        and Decimal(row["UnitPrice"]) > 0
    )


def money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def percent(numerator: int | Decimal, denominator: int | Decimal) -> str:
    return str(
        (Decimal(numerator) * HUNDRED / Decimal(denominator)).quantize(
            CENT, rounding=ROUND_HALF_UP
        )
    )


def additional_gold(raw: list[dict[str, str]]) -> dict[str, str]:
    """Recompute all new numeric measures over pinned rows with Decimal."""
    sales_rows = [row for row in raw if qualifies(row)]

    def sales(rows: list[dict[str, str]]) -> Decimal:
        return sum((amount(row) for row in rows), Decimal("0"))

    def qualifying_where(predicate) -> list[dict[str, str]]:
        return [row for row in sales_rows if predicate(row)]

    out: dict[str, dict[str, str]] = {}

    # Weekend and weekday aggregates. These are calendar dates; Saturday has
    # no qualifying source rows, but Sunday rows are in the weekend group.
    groups = {
        "weekend": qualifying_where(
            lambda row: date.fromisoformat(row["InvoiceDate"][:10]).weekday() >= 5
        ),
        "weekday": qualifying_where(
            lambda row: date.fromisoformat(row["InvoiceDate"][:10]).weekday() < 5
        ),
    }
    values: dict[str, str] = {}
    for label, members in groups.items():
        total = sales(members)
        values[f"{label}_lines"] = str(len(members))
        values[f"{label}_sales_gbp"] = money(total)
        values[f"{label}_invoices"] = str(len({row["InvoiceNo"] for row in members}))
        values[f"{label}_avg_line_gbp"] = money(total / len(members))
    out["retail-weekend-weekday-comparison"] = values

    # Inclusive calendar bands. Empty dates Jan 1-3 belong to the first band.
    values = {}
    for label, low, high in [
        ("days_01_07", "2011-01-01", "2011-01-07"),
        ("days_08_14", "2011-01-08", "2011-01-14"),
        ("days_15_21", "2011-01-15", "2011-01-21"),
        ("days_22_31", "2011-01-22", "2011-01-31"),
    ]:
        members = qualifying_where(
            lambda row, low=low, high=high: low <= row["InvoiceDate"][:10] <= high
        )
        values[f"{label}_lines"] = str(len(members))
        values[f"{label}_sales_gbp"] = money(sales(members))
    out["retail-calendar-week-bands"] = values

    # Earliest and latest qualifying calendar date, with each day's measures.
    date_groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in sales_rows:
        date_groups[row["InvoiceDate"][:10]].append(row)
    first_date, last_date = min(date_groups), max(date_groups)
    values = {}
    for label, day in (("first", first_date), ("last", last_date)):
        members = date_groups[day]
        values[f"{label}_day_lines"] = str(len(members))
        values[f"{label}_day_sales_gbp"] = money(sales(members))
    out["retail-qualifying-date-endpoints"] = values

    # Raw cross-tab. This case deliberately applies no qualifying-sales filter.
    values = {}
    for label, cancellation, negative in [
        ("cancel_negative_quantity_rows", True, True),
        ("noncancel_negative_quantity_rows", False, True),
        ("cancel_nonnegative_quantity_rows", True, False),
        ("noncancel_nonnegative_quantity_rows", False, False),
    ]:
        members = [
            row
            for row in raw
            if row["InvoiceNo"].upper().startswith("C") == cancellation
            and (int(row["Quantity"]) < 0) == negative
        ]
        values[label] = str(len(members))
    out["retail-cancellation-negative-quantity-crosstab"] = values

    # All-row signed row amounts use Quantity × UnitPrice, including negative
    # values. This is a descriptive line sum, not accounting net revenue.
    negative_rows = [row for row in raw if int(row["Quantity"]) < 0]
    out["retail-raw-signed-versus-qualifying-sales"] = {
        "raw_rows": str(len(raw)),
        "raw_signed_row_amount_gbp": money(sales(raw)),
        "raw_negative_quantity_rows": str(len(negative_rows)),
        "raw_negative_quantity_signed_amount_gbp": money(sales(negative_rows)),
        "qualifying_rows": str(len(sales_rows)),
        "qualifying_sales_gbp": money(sales(sales_rows)),
    }

    # Invoice bins are assigned after each row has passed the qualifying rules.
    invoice_lines: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in sales_rows:
        invoice_lines[row["InvoiceNo"]].append(row)
    single = [members for members in invoice_lines.values() if len(members) == 1]
    multiple = [members for members in invoice_lines.values() if len(members) >= 2]
    values = {}
    for label, invoices in (("single_line", single), ("multi_line", multiple)):
        values[f"{label}_invoice_count"] = str(len(invoices))
        values[f"{label}_qualifying_lines"] = str(
            sum(len(members) for members in invoices)
        )
        values[f"{label}_sales_gbp"] = money(
            sum((sales(members) for members in invoices), Decimal("0"))
        )
    out["retail-invoice-line-count-buckets"] = values

    total_sales = sales(sales_rows)
    total_lines = len(sales_rows)
    total_invoices = len(invoice_lines)
    out["retail-line-average-v-invoice-average"] = {
        "average_qualifying_line_gbp": money(total_sales / total_lines),
        "average_qualifying_invoice_gbp": money(total_sales / total_invoices),
    }

    # Country concentration, with deterministic tie-break by country label.
    country_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in sales_rows:
        country_rows[row["Country"]].append(row)
    country_rank = sorted(
        ((country, sales(members)) for country, members in country_rows.items()),
        key=lambda item: (-item[1], item[0]),
    )
    top_three_sales = sum((value for _, value in country_rank[:3]), Decimal("0"))
    out["retail-top-three-country-concentration"] = {
        "top_three_countries_sales_gbp": money(top_three_sales),
        "top_three_sales_share_percent": percent(top_three_sales, total_sales),
    }

    # Leading StockCode by qualifying sales; label is checked in the rubric.
    stock_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in sales_rows:
        stock_rows[row["StockCode"]].append(row)
    stock_rank = sorted(
        (
            (
                stock,
                sales(members),
                sum(int(r["Quantity"]) for r in members),
                len(members),
            )
            for stock, members in stock_rows.items()
        ),
        key=lambda item: (-item[1], item[0]),
    )
    _, stock_sales, stock_units, stock_lines = stock_rank[0]
    out["retail-leading-stock-code-by-sales"] = {
        "leading_stock_sales_gbp": money(stock_sales),
        "leading_stock_qualifying_units": str(stock_units),
        "leading_stock_qualifying_lines": str(stock_lines),
    }

    # UnitPrice is in GBP per item; comparisons use exact Decimal values.
    values = {}
    for label, predicate in (
        ("unit_price_under_1", lambda row: Decimal(row["UnitPrice"]) < 1),
        ("unit_price_1_or_more", lambda row: Decimal(row["UnitPrice"]) >= 1),
    ):
        members = [row for row in sales_rows if predicate(row)]
        subtotal = sales(members)
        values[f"{label}_lines"] = str(len(members))
        values[f"{label}_sales_gbp"] = money(subtotal)
        values[f"{label}_sales_share_percent"] = percent(subtotal, total_sales)
    out["retail-unit-price-buckets"] = values

    # Quantity thresholds are line quantities, with 100 assigned to upper bin.
    values = {}
    for label, predicate in (
        ("quantity_under_100", lambda row: int(row["Quantity"]) < 100),
        ("quantity_100_or_more", lambda row: int(row["Quantity"]) >= 100),
    ):
        members = [row for row in sales_rows if predicate(row)]
        subtotal = sales(members)
        values[f"{label}_lines"] = str(len(members))
        values[f"{label}_units"] = str(sum(int(row["Quantity"]) for row in members))
        values[f"{label}_sales_gbp"] = money(subtotal)
        values[f"{label}_sales_share_percent"] = percent(subtotal, total_sales)
    out["retail-quantity-size-buckets"] = values

    # Conditional missing-customer comparisons; each population has its own
    # line-count and sales-value denominator.
    values = {}
    populations = {
        "uk": lambda row: row["Country"] == "United Kingdom",
        "other_countries": lambda row: row["Country"] != "United Kingdom",
    }
    for label, predicate in populations.items():
        members = [row for row in sales_rows if predicate(row)]
        missing = [row for row in members if not row["CustomerID"].strip()]
        subtotal = sales(members)
        missing_sales = sales(missing)
        values[f"{label}_qualifying_lines"] = str(len(members))
        values[f"{label}_missing_customer_lines"] = str(len(missing))
        values[f"{label}_missing_customer_line_share_percent"] = percent(
            len(missing), len(members)
        )
        values[f"{label}_missing_customer_sales_gbp"] = money(missing_sales)
        values[f"{label}_missing_customer_sales_share_percent"] = percent(
            missing_sales, subtotal
        )
    out["retail-conditional-customer-missing-shares"] = values

    return {key: value for values in out.values() for key, value in values.items()}


def additional_cases(
    gold: dict[str, str], catalog: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Reviewable definitions plus independently recomputed numeric answers."""
    cases = json.loads(
        (Path(__file__).with_name("real_v2_additions.json")).read_text()
    )["cases"]
    keys = set()
    for case in cases:
        case["sources"] = [deepcopy(catalog[s["alias"]]) for s in case["sources"]]
        for expectation in case["expectations"]["calculations"]:
            key = expectation["key"]
            keys.add(key)
            if Decimal(expectation["value"]) != Decimal(gold[key]):
                raise ValueError(f"Reviewed gold differs from computed value: {key}")
            expectation["value"] = gold[key]
    if keys != set(gold):
        raise ValueError("Computed and declared numeric keys differ")
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--manifest-output", type=Path, default=MANIFEST)
    args = parser.parse_args()
    inherited, rows = load_inputs()
    gold = additional_gold(rows)
    additions = additional_cases(gold, source_catalog(inherited))
    cases = validate_additions(inherited, additions)
    payload = (
        json.dumps({"schema_version": 1, "cases": cases}, ensure_ascii=False, indent=2)
        + "\n"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    manifest = {
        "version": "real-v2",
        "revision": "2026-10-06",
        "base_inventory": {
            "path": "evals/cases/real-v1.json",
            "sha256": BASE_SHA256,
            "inherited_cases": 30,
        },
        "case_inventory": {
            "path": "evals/cases/real-v2.json",
            "sha256": hashlib.sha256(payload.encode()).hexdigest(),
        },
        "shared_source_manifest": {
            "path": "real-v1/manifest.json",
            "sha256": v1.sha256(v1.PACK_ROOT / "manifest.json"),
        },
        "sources": list({s["path"]: s for c in cases for s in c["sources"]}.values()),
        "additional_gold": gold,
        "coverage": {
            "total_cases": len(cases),
            "added_cases": len(additions),
            "retail_cases": sum("retail" in c["tags"] for c in cases),
            "survey_cases": sum("survey" in c["tags"] for c in cases),
            "languages": {
                lang: sum(c["language"] == lang for c in cases)
                for lang in sorted({c["language"] for c in cases})
            },
        },
        "review": {
            "provenance": "unreviewed",
            "method": "Luna draft and primary agent source/independent integer-SQL review; not independent human or native-speaker certification",
        },
        "limits": [
            "Document factuality and language require manual review",
            "Grouped/category values and artifact contents require manual review",
            "No model trials or calibrated judges",
            "More cases do not establish statistical reliability; source families remain shared",
        ],
    }
    args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {len(cases)} cases, including {len(additions)} additions, to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
