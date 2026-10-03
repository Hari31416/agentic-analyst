"""Bounded flat JSON/Parquet adapters; originals remain immutable."""

import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

MAX_CELLS = 1_000_000
MAX_DECODED_BYTES = 100 * 1024 * 1024


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, bytes)):
        raise ValueError("Only flat scalar records are supported")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        import math

        if not math.isfinite(value):
            raise ValueError("Nonfinite values are not supported")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("Nonfinite values are not supported")
    return str(value)


def records_csv(extension: str, content: bytes) -> bytes:
    """Decode under physical/expanded bounds, preserving scalar precision as text."""
    if extension == "json":
        records = json.loads(
            content,
            parse_float=Decimal,
            object_pairs_hook=_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError("Nonfinite JSON number")
            ),
        )
        if (
            not isinstance(records, list)
            or not records
            or any(not isinstance(r, dict) for r in records)
        ):
            raise ValueError("JSON must be a nonempty array of flat records")
        headers = list(dict.fromkeys(key for row in records for key in row))
        if len(headers) > 1000 or len(records) * len(headers) > MAX_CELLS:
            raise ValueError("Record expansion exceeds limits")
    elif extension == "parquet":
        import pyarrow as pa  # type: ignore[import-untyped]
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        if not content.startswith(b"PAR1") or not content.endswith(b"PAR1"):
            raise ValueError("Parquet signature is invalid")
        file = pq.ParquetFile(pa.BufferReader(content))
        headers = file.schema_arrow.names
        if (
            not headers
            or len(headers) > 1000
            or len(headers) != len(set(headers))
            or file.metadata.num_rows * len(headers) > MAX_CELLS
        ):
            raise ValueError("Parquet dimensions exceed limits")
        expanded = sum(
            file.metadata.row_group(i).total_byte_size
            for i in range(file.metadata.num_row_groups)
        )
        if expanded > MAX_DECODED_BYTES or expanded > max(
            len(content) * 100, 1024 * 1024
        ):
            raise ValueError("Parquet expansion exceeds limits")
        for field in file.schema_arrow:
            if (
                pa.types.is_nested(field.type)
                or pa.types.is_binary(field.type)
                or pa.types.is_large_binary(field.type)
            ):
                raise ValueError("Only flat scalar Parquet columns are supported")
        records = []
        decoded = 0
        for batch in file.iter_batches(batch_size=1000):
            decoded += batch.nbytes
            if decoded > MAX_DECODED_BYTES:
                raise ValueError("Parquet decoded size exceeds limits")
            records.extend(batch.to_pylist())
    else:
        raise ValueError("Unsupported record adapter")
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(headers)
    for row in records:
        writer.writerow([_scalar(row.get(c)) for c in headers])
        if out.tell() > MAX_DECODED_BYTES:
            raise ValueError("Canonical dataset exceeds limits")
    return out.getvalue().encode("utf-8")
