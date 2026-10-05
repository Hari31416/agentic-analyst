"""Build the trusted DuckDB launcher; it executes only inside the microVM."""

from typing import Any


def table_alias(dataset_id: str) -> str:
    from uuid import UUID

    return "data_" + UUID(dataset_id).hex


def query_program(
    sql: str,
    dataset_ids: list[str],
    max_rows: int,
    timeout: int,
    table_names: dict[str, str] | None = None,
) -> str:
    config: dict[str, Any] = {
        "sql": sql,
        "tables": [
            {
                "name": (table_names or {}).get(identity, table_alias(identity)),
                "path": f"/workspace/inputs/{identity}.csv",
            }
            for identity in dataset_ids
        ],
        "max_rows": max_rows,
        "timeout": timeout,
    }
    return "CONFIG = " + repr(config) + "\n" + _PROGRAM


_PROGRAM = """import csv
import datetime
import decimal
import json
import math
import threading
from pathlib import Path
import duckdb

def cell(value):
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, int) and abs(value) > 2**53:
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, bytes):
        return value.hex()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)

conn = duckdb.connect(':memory:', config={
    'memory_limit': '512MB', 'threads': '1',
    'autoinstall_known_extensions': 'false', 'autoload_known_extensions': 'false',
})
timer = threading.Timer(CONFIG['timeout'], conn.interrupt)
timer.daemon = True
timer.start()
try:
    for item in CONFIG['tables']:
        # Only server-owned aliases and staged UUID paths enter this setup.
        conn.execute('CREATE TABLE "' + item['name'] + '" AS SELECT * FROM read_csv(?, header=true, all_varchar=true)', [item['path']])
    conn.execute('SET enable_external_access=false')
    conn.execute('SET lock_configuration=true')
    cursor = conn.execute(CONFIG['sql'])
    originals = [item[0] for item in cursor.description]
    columns = []
    for original in originals:
        name = original
        suffix = 2
        while name in columns:
            name = original + '__' + str(suffix)
            suffix += 1
        columns.append(name)
    rows = []
    truncated = False
    byte_limit = False
    # Bound retained output after the aggregate has consumed its full inputs.
    with Path('result.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        retained = 0
        for number in range(CONFIG['max_rows'] + 1):
            row = cursor.fetchone()
            if row is None:
                break
            if number == CONFIG['max_rows']:
                truncated = True
                break
            converted = [cell(value) for value in row]
            writer.writerow(converted)
            retained += 1
            if len(rows) < 20:
                rows.append(dict(zip(columns, [value[:2000] if isinstance(value, str) else value for value in converted])))
            stream.flush()
            if stream.tell() > 7 * 1024 * 1024:
                truncated = True
                byte_limit = True
                break
    metadata = {'columns': columns, 'column_mapping': [{'name': name, 'original_name': original} for name, original in zip(columns, originals)], 'rows': rows, 'row_count': retained, 'truncated': truncated, 'byte_limit_reached': byte_limit, 'duckdb_version': duckdb.__version__, 'input_types': 'VARCHAR; numeric/date casts are explicit in SQL', 'sample_rows': len(rows)}
    Path('query-result.json').write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'row_count': retained, 'truncated': truncated}))
finally:
    timer.cancel()
    conn.close()
"""
