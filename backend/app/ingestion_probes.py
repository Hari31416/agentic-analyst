"""Record one local OCR/layout fixture pass without calling the chat model."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import subprocess
import unicodedata
from pathlib import Path
from time import perf_counter

from app.sources.documents import extract_document

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "evals" / "fixtures" / "v2"


def distance(left: list[str] | str, right: list[str] | str) -> int:
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(
                min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b))
            )
        previous = current
    return previous[-1]


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def main() -> None:
    truth = json.loads((FIXTURES / "ground-truth.json").read_text())
    records = []
    for name, language in [
        ("scanned-en", "en"),
        ("scanned-hi", "hi"),
        ("rotated-en", "en"),
        ("noisy-hi", "hi"),
    ]:
        path = FIXTURES / (name + ".pdf")
        start = perf_counter()
        blocks, warnings, languages = extract_document(path.name, path.read_bytes())
        expected = normalized(truth["scans"][language])
        observed = normalized(
            "\n".join(block.text for block in blocks if block.kind != "heading")
        )
        expected_numbers = re.findall(r"\b\d+(?:\.\d+)?\b", expected)
        observed_numbers = re.findall(r"\b\d+(?:\.\d+)?\b", observed)
        records.append(
            {
                "fixture": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "duration_seconds": round(perf_counter() - start, 3),
                "cer": distance(expected, observed) / max(1, len(expected)),
                "wer": distance(expected.split(), observed.split())
                / max(1, len(expected.split())),
                "numeral_errors": [
                    number
                    for number in expected_numbers
                    if number not in observed_numbers
                ],
                "identifier_errors": [
                    name
                    for name in re.findall(r"APP-\d+", expected)
                    if name not in observed
                ],
                "locations": [block.location for block in blocks],
                "warnings": warnings,
                "languages": languages,
                "excerpt": observed[:2000],
            }
        )
    mixed_path = FIXTURES / "mixed.pdf"
    blocks, warnings, _ = extract_document(mixed_path.name, mixed_path.read_bytes())
    mixed = {
        "routing": [
            {
                "page": block.location.get("page"),
                "extractor": block.location.get("extractor"),
            }
            for block in blocks
        ],
        "warnings": warnings,
    }
    table_path = FIXTURES / "wide-table.pdf"
    blocks, warnings, _ = extract_document(
        table_path.name, table_path.read_bytes(), profile="layout"
    )
    rows = [block.location["cells"] for block in blocks if block.kind == "table_row"]
    expected_rows = [truth["wide_table"]["headers"]] + truth["wide_table"]["rows"]
    correct = sum(
        1
        for actual, expected in zip(rows, expected_rows)
        for a, e in zip(actual, expected)
        if a == e
    )
    expected_count = sum(len(row) for row in expected_rows)
    table = {
        "cell_accuracy": correct / expected_count,
        "extracted_rows": len(rows),
        "expected_rows": len(expected_rows),
        "warnings": warnings,
        "grant_total": (
            sum(int(row[3]) for row in rows[1:])
            if len(rows) == len(expected_rows)
            else None
        ),
    }
    report = {
        "phase": "04",
        "validation_focus": "Code logic, routing, bounds and provenance. OCR accuracy is descriptive, not a quality gate, per user instruction.",
        "ocr_engine": subprocess.run(
            ["tesseract", "--version"], capture_output=True, text=True, check=True
        ).stdout.splitlines()[0],
        "renderer": importlib.metadata.version("pypdfium2"),
        "layout": importlib.metadata.version("pdfplumber"),
        "cases": records,
        "mixed": mixed,
        "table": table,
    }
    target = ROOT / "evals" / "reports" / "phase04-2026-10-02.json"
    language_inventory = subprocess.run(
        ["tesseract", "--list-langs"], capture_output=True, text=True, check=True
    ).stdout
    asset_directory = re.search(r'in "([^"]+)"', language_inventory)
    if asset_directory:
        asset_root = Path(asset_directory.group(1))
        report["ocr_assets"] = {
            name: hashlib.sha256(
                (asset_root / f"{name}.traineddata").read_bytes()
            ).hexdigest()
            for name in ("eng", "hin", "osd")
            if (asset_root / f"{name}.traineddata").is_file()
        }
    # Retain the independent microVM milestone instead of rerunning it for OCR.
    if target.is_file():
        previous = json.loads(target.read_text())
        for key in ("microvm", "limitations", "quality_gate"):
            if key in previous:
                report[key] = previous[key]
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(target),
                "ocr_cases": len(records),
                "mixed": mixed,
                "table": table,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
