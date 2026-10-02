"""Table candidates backed by persisted document extraction blocks."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Dataset, Document, DocumentBlock, Source
from app.storage.filesystem import Storage

PREVIEW_ROWS = 10
PREVIEW_MAX_BYTES = 64 * 1024
MAX_TABLE_BYTES = 20 * 1024 * 1024
TABLE_SCHEMA_VERSION = "document-table-v1"
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_GROUPED_NUMBER_RE = re.compile(
    r"^[+-]?(?:(?:\d{1,3}(?:,\d{3})+)|(?:\d{1,2}(?:,\d{2})*,\d{3})|\d+)(?:\.\d*)?$|^[+-]?\.\d+$"
)
_CURRENCY_RE = re.compile(r"^(?:INR|USD|EUR|GBP)\s*|[₹$€£\s]", re.IGNORECASE)
_IDENTIFIER_RE = re.compile(
    r"\b(?:id|identifier|code|ref(?:erence)?|account|phone|postal|zip|pin|applicant)\b",
    re.IGNORECASE,
)
_LOCATION_KEYS = (
    "page",
    "slide",
    "shape",
    "bbox",
    "cell_id",
    "origin",
    "extractor",
    "ocr_confidence",
    "ocr_confidence_type",
    "ocr_rotation_degrees",
    "char_start",
    "char_end",
)


class DocumentTableError(ValueError):
    """A table candidate cannot be previewed or accepted."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CandidateRow:
    row_index: int
    block_id: str
    page: int | None
    cells: list[str]
    location: dict[str, Any]


@dataclass(frozen=True)
class TableCandidate:
    table_id: str
    number: int
    title: str | None
    page: int | None
    slide: int | None
    rows: list[CandidateRow]
    warnings: list[str]


def _table_id(number: int) -> str:
    return f"table-{number}"


def _table_number(table_id: str) -> int:
    match = re.fullmatch(r"table-(\d{1,6})", table_id)
    if match is None:
        raise DocumentTableError("table_not_found", "Table candidate was not found.")
    return int(match.group(1))


def _candidates(blocks: list[DocumentBlock]) -> list[TableCandidate]:
    grouped: dict[int, list[CandidateRow]] = {}
    titles: dict[int, str | None] = {}
    pages: dict[int, int | None] = {}
    slides: dict[int, int | None] = {}
    warnings: dict[int, list[str]] = {}
    for block in blocks:
        location = block.location if isinstance(block.location, dict) else {}
        try:
            number = int(location["table"])
        except (KeyError, TypeError, ValueError):
            continue
        if number < 0 or block.kind not in {"table_row", "table"}:
            continue
        cells = location.get("cells")
        if not isinstance(cells, list) or any(
            not isinstance(cell, str) for cell in cells
        ):
            warnings.setdefault(number, []).append(
                "Table cells are unavailable in the persisted extraction."
            )
            continue
        try:
            row_index = int(location.get("row", len(grouped.get(number, []))))
        except (TypeError, ValueError):
            row_index = len(grouped.get(number, []))
        page_value = location.get("page")
        page = page_value if isinstance(page_value, int) and page_value > 0 else None
        grouped.setdefault(number, []).append(
            CandidateRow(
                row_index,
                block.id,
                page,
                [cell.strip() for cell in cells],
                location,
            )
        )
        if number not in titles:
            titles[number] = block.heading
            pages[number] = page
            slide_value = location.get("slide")
            slides[number] = (
                slide_value
                if isinstance(slide_value, int) and slide_value > 0
                else None
            )
    result = []
    for number in sorted(set(grouped) | set(warnings)):
        rows = sorted(grouped.get(number, []), key=lambda row: row.row_index)
        warning_list = list(dict.fromkeys(warnings.get(number, [])))
        if not rows:
            warning_list.append("No complete cell rows are available for this table.")
        result.append(
            TableCandidate(
                table_id=_table_id(number),
                number=number,
                title=titles.get(number),
                page=pages.get(number),
                slide=slides.get(number),
                rows=rows,
                warnings=warning_list,
            )
        )
    return result


def _column_names(header: list[str], width: int) -> list[str]:
    names: list[str] = []
    used: set[str] = set()
    for index in range(width):
        base = (
            (header[index] if index < len(header) else "").strip()
            or f"Column {index + 1}"
        )[:255]
        name = base
        suffix = 2
        while name in used:
            ending = f" ({suffix})"
            name = base[: 255 - len(ending)] + ending
            suffix += 1
        used.add(name)
        names.append(name)
    return names


def _parse_value(raw: str, column_name: str = "") -> tuple[str, Any]:
    value = raw.strip()
    if not value:
        return "null", None
    lowered = value.casefold()
    if lowered in {"true", "yes"}:
        return "boolean", True
    if lowered in {"false", "no"}:
        return "boolean", False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return "date", date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    if _IDENTIFIER_RE.search(column_name) or re.fullmatch(r"0\d+", value):
        return "text", value
    # Commas are accepted only in conventional three-digit groups. Malformed
    # grouping stays text so a value such as "1,2" is never silently changed.
    numeric = _CURRENCY_RE.sub("", value)
    if "," in numeric:
        if not _GROUPED_NUMBER_RE.fullmatch(numeric):
            return "text", value
        numeric = numeric.replace(",", "")
    if _NUMBER_RE.fullmatch(numeric):
        try:
            number = Decimal(numeric)
            if number == number.to_integral_value():
                return "integer", int(number)
            return "number", format(number, "f")
        except (InvalidOperation, OverflowError):
            pass
    return "text", value


def _cell_location(row: CandidateRow, column: int) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        key: row.location[key] for key in _LOCATION_KEYS if key in row.location
    }
    cell_locations = row.location.get("cell_locations")
    if isinstance(cell_locations, list) and column < len(cell_locations):
        cell_location = cell_locations[column]
        if isinstance(cell_location, dict):
            provenance.update(
                {
                    str(key): value
                    for key, value in cell_location.items()
                    if key in {"cell_id", "id", "bbox", "origin", "confidence"}
                }
            )
    if "id" in provenance:
        provenance["cell_id"] = provenance.pop("id")
    return provenance


def _cell_id(row: CandidateRow, column: int) -> str | None:
    value = _cell_location(row, column).get("cell_id")
    return value if isinstance(value, str) and value.strip() else None


def _invalid_grouping(value: str) -> bool:
    if "," not in value:
        return False
    cleaned = _CURRENCY_RE.sub("", value)
    numeric_like = re.fullmatch(r"[+\-\d.,\s₹$€£]+", value) is not None
    return numeric_like and not _GROUPED_NUMBER_RE.fullmatch(cleaned)


def _currency_unit(value: str) -> str | None:
    match = re.match(r"\s*(INR|USD|EUR|GBP)\b", value, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    for symbol, unit in (("₹", "INR"), ("$", "USD"), ("€", "EUR"), ("£", "GBP")):
        if symbol in value:
            return unit
    return None


def _table_values(
    candidate: TableCandidate,
) -> tuple[list[str], list[dict[str, Any]], list[CandidateRow]]:
    if not candidate.rows:
        raise DocumentTableError(
            "table_cells_unavailable", "Table cells are unavailable."
        )
    width = max((len(row.cells) for row in candidate.rows), default=0)
    if width == 0:
        raise DocumentTableError("table_cells_unavailable", "Table has no cells.")
    header = candidate.rows[0].cells
    columns = _column_names(header, width)
    records: list[dict[str, Any]] = []
    data_rows = candidate.rows[1:]
    for row in data_rows:
        values = list(row.cells[:width]) + [""] * max(0, width - len(row.cells))
        records.append(
            {
                "row": row,
                "values": [
                    _parse_value(value, columns[index])
                    for index, value in enumerate(values)
                ],
                "raw": values,
            }
        )
    schema: list[dict[str, Any]] = []
    for index, name in enumerate(columns):
        kinds = [record["values"][index][0] for record in records]
        observed = [kind for kind in kinds if kind != "null"]
        kind = "text" if not observed else observed[0]
        if any(value != kind for value in observed):
            kind = "text"
        column_schema: dict[str, Any] = {
            "name": name,
            "type": kind,
            "nullable": "null" in kinds,
        }
        units = {
            unit
            for record in records
            if (unit := _currency_unit(record["raw"][index])) is not None
        }
        if len(units) == 1:
            column_schema["unit"] = next(iter(units))
        schema.append(column_schema)
    return columns, schema, data_rows


def _candidate_warnings(
    document: Document, candidate: TableCandidate, columns: list[str]
) -> list[str]:
    raw_warnings = (document.details or {}).get("warnings", [])
    warnings = (
        [warning for warning in raw_warnings if isinstance(warning, str)]
        if isinstance(raw_warnings, list)
        else []
    )
    warnings.extend(candidate.warnings)
    for row in candidate.rows[1:]:
        if any(_invalid_grouping(value) for value in row.cells):
            warnings.append("Invalid numeric grouping was preserved as text.")
            break
    for column, name in enumerate(columns):
        observed = {
            kind
            for row in candidate.rows[1:]
            if column < len(row.cells)
            for kind, _ in [_parse_value(row.cells[column], name)]
            if kind != "null"
        }
        if len(observed) > 1:
            warnings.append(f"Mixed cell types in {name} were preserved as text.")
        units = {
            unit
            for row in candidate.rows[1:]
            if column < len(row.cells)
            if (unit := _currency_unit(row.cells[column])) is not None
        }
        if len(units) > 1:
            warnings.append(f"Multiple currencies were found in {name}.")
    return list(dict.fromkeys(warnings))


def get_document_tables(session: Session, document_id: str) -> dict[str, Any] | None:
    document = session.get(Document, document_id)
    if document is None:
        return None
    rows = list(
        session.scalars(
            select(DocumentBlock)
            .where(DocumentBlock.document_id == document.id)
            .order_by(DocumentBlock.ordinal)
        )
    )
    tables = []
    used = 0
    for candidate in _candidates(rows):
        try:
            columns, schema, data_rows = _table_values(candidate)
        except DocumentTableError:
            columns, schema, data_rows = [], [], []
        preview: list[dict[str, Any]] = []
        for record in [r for r in candidate.rows[1 : PREVIEW_ROWS + 1]]:
            cells = []
            for column, raw_value in enumerate(record.cells[: len(columns)]):
                kind, value = _parse_value(
                    raw_value, columns[column] if column < len(columns) else ""
                )
                cells.append(
                    {
                        "value": value,
                        "type": kind,
                        "provenance": {
                            "block_id": record.block_id,
                            "row": record.row_index,
                            "column": column,
                            "page": record.page,
                            "raw_value": raw_value[:2000],
                            **_cell_location(record, column),
                        },
                    }
                )
            item = {"row_index": record.row_index, "cells": cells}
            size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            if used + size > PREVIEW_MAX_BYTES:
                break
            used += size
            preview.append(item)
        accepted = (
            (document.details or {}).get("accepted_tables", {}).get(candidate.table_id)
        )
        tables.append(
            {
                "table_id": candidate.table_id,
                "title": candidate.title,
                "page": candidate.page,
                "slide": candidate.slide,
                "row_count": max(0, len(candidate.rows) - 1),
                "column_count": len(columns),
                "columns": schema,
                "preview": preview,
                "warnings": _candidate_warnings(document, candidate, columns),
                "accepted_dataset_id": (
                    accepted.get("dataset_id") if isinstance(accepted, dict) else None
                ),
            }
        )
    return {
        "document_id": document.id,
        "state": document.state,
        "tables": tables,
    }


def accept_document_table(
    session: Session, storage: Storage, document_id: str, table_id: str
) -> dict[str, Any] | None:
    # Serialize accept requests so concurrent retries publish one accepted dataset.
    document = session.scalar(
        select(Document).where(Document.id == document_id).with_for_update()
    )
    if document is None:
        return None
    if document.state != "ready":
        raise DocumentTableError(
            "document_not_ready", "Document extraction is not ready."
        )
    details = dict(document.details or {})
    accepted_tables = dict(details.get("accepted_tables") or {})
    existing = accepted_tables.get(table_id)
    if isinstance(existing, dict):
        source = session.get(Source, existing.get("source_id"))
        dataset = session.get(Dataset, existing.get("dataset_id"))
        if source is not None and dataset is not None:
            return _acceptance_view(document, source, dataset, table_id)

    table_number = _table_number(table_id)
    blocks = list(
        session.scalars(
            select(DocumentBlock)
            .where(DocumentBlock.document_id == document.id)
            .order_by(DocumentBlock.ordinal)
        )
    )
    candidate = next(
        (item for item in _candidates(blocks) if item.number == table_number), None
    )
    if candidate is None:
        raise DocumentTableError("table_not_found", "Table candidate was not found.")
    if candidate.warnings:
        raise DocumentTableError("table_cells_unavailable", candidate.warnings[0])
    columns, schema, data_rows = _table_values(candidate)
    table_warnings = _candidate_warnings(document, candidate, columns)
    records: list[dict[str, Any]] = []
    for row in data_rows:
        padded = list(row.cells[: len(columns)]) + [""] * max(
            0, len(columns) - len(row.cells)
        )
        records.append(
            {
                "row": row,
                "raw": padded,
                "values": [
                    _parse_value(value, columns[index])
                    for index, value in enumerate(padded)
                ],
            }
        )
    if not records:
        raise DocumentTableError("table_empty", "Table has no data rows to accept.")
    # Avoid claiming a single type when cells in the column disagree.
    for index, column in enumerate(schema):
        observed = [
            record["values"][index][0]
            for record in records
            if record["values"][index][0] != "null"
        ]
        if observed and any(kind != observed[0] for kind in observed):
            column["type"] = "text"
            for record in records:
                kind, value = record["values"][index]
                record["values"][index] = (
                    "text",
                    record["raw"][index] if kind != "null" else None,
                )
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(columns)
    for record in records:
        writer.writerow([value for _, value in record["values"]])
    content = output.getvalue().encode("utf-8")
    if len(content) > MAX_TABLE_BYTES:
        raise DocumentTableError(
            "table_too_large", "Accepted table exceeds the storage limit."
        )

    original_source = session.get(Source, document.source_id)
    if original_source is None:
        raise DocumentTableError("source_not_found", "Document source is unavailable.")
    new_source_id = str(uuid4())
    identity = f"document:{document.id}:table:{table_number}"
    lineage = [
        f"source:{original_source.id}@v{document.source_version}",
        f"document:{document.id}@v{document.source_version}",
        *[
            item
            for row in candidate.rows
            for item in (
                f"document_block:{row.block_id}",
                *[
                    f"document_cell:{_cell_id(row, column) or f'{row.block_id}:{column}'}"
                    for column in range(len(row.cells))
                ],
            )
        ],
    ]
    storage_key = f"derived/{original_source.workspace_id}/{new_source_id}/table-{table_number}.csv"
    stored = storage.put(storage_key, content)
    derived_source = Source(
        id=new_source_id,
        workspace_id=original_source.workspace_id,
        kind="csv",
        version=1,
        display_name=(
            f"{original_source.display_name} — "
            f"{candidate.title or f'Table {table_number + 1}'}.csv"
        )[:255],
        state="ready",
        storage_key=stored.key,
        content_hash=stored.sha256,
        schema_version=TABLE_SCHEMA_VERSION,
        details={
            "designation": "derived",
            "media_type": "text/csv",
            "byte_size": stored.byte_size,
            "sha256": stored.sha256,
            "lineage": lineage,
            "document_id": document.id,
            "table_id": table_id,
            "warnings": table_warnings,
        },
    )
    session.add(derived_source)
    session.flush()
    dataset_details = {
        "identity": identity,
        "row_count": len(records),
        "columns": schema,
        "table_title": candidate.title,
        "table_id": table_id,
        "source_document_id": document.id,
        "source_document_version": document.source_version,
        "extraction_warnings": [
            warning
            for warning in (document.details or {}).get("warnings", [])
            if isinstance(warning, str)
        ],
        "lineage": lineage,
        "warnings": table_warnings,
        "header_provenance": [
            {
                "row": candidate.rows[0].row_index,
                "column": column,
                "block_id": candidate.rows[0].block_id,
                "page": candidate.rows[0].page,
                **_cell_location(candidate.rows[0], column),
            }
            for column in range(len(candidate.rows[0].cells))
        ],
        "cell_provenance": [
            {
                "row": record["row"].row_index,
                "column": column,
                "block_id": record["row"].block_id,
                "page": record["row"].page,
                **_cell_location(record["row"], column),
            }
            for record in records
            for column in range(len(record["row"].cells))
        ],
        "details": {"row_count": len(records), "columns": schema},
    }
    dataset = Dataset(
        source_id=derived_source.id,
        source_version=derived_source.version,
        identity=identity,
        schema_version=TABLE_SCHEMA_VERSION,
        details=dataset_details,
        storage_key=stored.key,
        designation="derived",
        lineage=[*lineage, f"source:{derived_source.id}@v1", f"sha256:{stored.sha256}"],
    )
    session.add(dataset)
    session.flush()
    accepted_tables[table_id] = {
        "source_id": derived_source.id,
        "dataset_id": dataset.id,
        "accepted_at": "explicit_user_acceptance",
    }
    details["accepted_tables"] = accepted_tables
    document.details = details
    session.commit()
    session.refresh(derived_source)
    session.refresh(dataset)
    session.refresh(document)
    return _acceptance_view(document, derived_source, dataset, table_id)


def _acceptance_view(
    document: Document, source: Source, dataset: Dataset, table_id: str
) -> dict[str, Any]:
    return {
        "document_id": document.id,
        "table_id": table_id,
        "dataset_id": dataset.id,
        "source_id": source.id,
        "identity": dataset.identity,
        "schema_version": dataset.schema_version,
        "designation": dataset.designation,
        "lineage": dataset.lineage,
        "details": dataset.details,
    }
