"""Bounded, read-only connectors for user-selected PostgreSQL/MySQL sources."""

from __future__ import annotations

import ssl
import json
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, time as time_of_day
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID

import pymysql
from cryptography.fernet import Fernet, InvalidToken
import sqlglot
from sqlglot import exp
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Connection as SqlAlchemyConnection
from sqlalchemy.engine import URL, Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Connection, Dataset, Source
from app.policy.sql import SqlPolicyError, validate_sql

_DEFAULT_PORT = {"postgresql": 5432, "mysql": 3306}
_MAX_SAFE_INTEGER = 2**53 - 1
_SSL_MODES = {"disable", "prefer", "require", "verify-ca", "verify-full"}


class ConnectorError(RuntimeError):
    """Bounded connector failure safe to expose to application callers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    row_count: int
    column_mapping: list[dict[str, str]]
    truncated_reason: str | None = None


class QueryControl:
    """Thread-safe cancellation handle for a live database statement."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel_callback: Callable[[], None] | None = None
        self._cancelled = False

    def register(self, callback: Callable[[], None]) -> None:
        run_now = False
        with self._lock:
            if self._cancelled:
                run_now = True
            else:
                self._cancel_callback = callback
        if run_now:
            callback()

    def clear(self) -> None:
        with self._lock:
            self._cancel_callback = None

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            callback = self._cancel_callback
            self._cancel_callback = None
        if callback is not None:
            try:
                callback()
            except Exception:
                # Statement deadlines and closing the checked-out connection
                # remain the bounded fallback when server cancellation fails.
                return


def encrypt_credentials(password: str, settings: Settings) -> str:
    key = settings.database_encryption_key
    if key is None:
        raise ConnectorError(
            "credential_encryption_unavailable",
            "Set DATABASE_ENCRYPTION_KEY before saving database credentials.",
        )
    return Fernet(key.get_secret_value().encode()).encrypt(password.encode()).decode()


def decrypt_credentials(ciphertext: str, settings: Settings) -> str:
    key = settings.database_encryption_key
    if key is None:
        raise ConnectorError(
            "credential_encryption_unavailable",
            "Database credentials cannot be decrypted without DATABASE_ENCRYPTION_KEY.",
        )
    try:
        return (
            Fernet(key.get_secret_value().encode())
            .decrypt(ciphertext.encode())
            .decode()
        )
    except (InvalidToken, ValueError):
        raise ConnectorError(
            "credential_decryption_failed",
            "Saved database credentials are unavailable.",
        ) from None


def normalize_options(options: dict[str, Any] | None) -> dict[str, Any]:
    values = dict(options or {})
    allowed = {"ssl_mode", "connect_timeout_seconds"}
    if set(values) - allowed:
        raise ConnectorError(
            "invalid_connection_options", "Unsupported connection option."
        )
    ssl_mode = str(values.get("ssl_mode", "verify-full")).lower()
    if ssl_mode not in _SSL_MODES:
        raise ConnectorError("invalid_connection_options", "Unsupported TLS mode.")
    timeout = values.get("connect_timeout_seconds", 5)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int)
        or not 1 <= timeout <= 15
    ):
        raise ConnectorError(
            "invalid_connection_options", "Connection timeout must be 1–15 seconds."
        )
    return {"ssl_mode": ssl_mode, "connect_timeout_seconds": timeout}


def _ssl_context(mode: str) -> ssl.SSLContext | None:
    if mode == "disable":
        return None
    context = ssl.create_default_context()
    if mode == "require" or mode == "prefer":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif mode == "verify-ca":
        context.check_hostname = False
    return context


def _engine(
    dialect: str,
    host: str,
    port: int,
    database_name: str,
    username: str,
    password: str,
    options: dict[str, Any],
) -> Engine:
    if dialect not in _DEFAULT_PORT:
        raise ConnectorError(
            "unsupported_dialect", "Only PostgreSQL and MySQL are supported."
        )
    normalized = normalize_options(options)
    connect_timeout = normalized["connect_timeout_seconds"]
    ssl_mode = normalized["ssl_mode"]
    driver = "postgresql+psycopg" if dialect == "postgresql" else "mysql+pymysql"
    connect_args: dict[str, Any] = {"connect_timeout": connect_timeout}
    if dialect == "postgresql":
        pg_ssl_mode = {
            "prefer": "prefer",
            "require": "require",
            "verify-ca": "verify-ca",
            "verify-full": "verify-full",
            "disable": "disable",
        }[ssl_mode]
        connect_args["sslmode"] = pg_ssl_mode
        connect_args["options"] = "-c statement_timeout=15000"
    else:
        connect_args["read_timeout"] = 35
        connect_args["write_timeout"] = 5
        context = _ssl_context(ssl_mode)
        if context is None:
            connect_args["ssl_disabled"] = True
        else:
            connect_args["ssl"] = context
    url = URL.create(
        driver,
        username=username,
        password=password,
        host=host,
        port=port,
        database=database_name,
    )
    return create_engine(
        url,
        connect_args=connect_args,
        pool_size=1,
        max_overflow=0,
        pool_timeout=connect_timeout,
        pool_pre_ping=True,
        pool_recycle=30,
    )


def _credentials(connection: Connection, settings: Settings) -> str:
    return decrypt_credentials(connection.encrypted_credentials, settings)


def test_connection(
    *,
    dialect: str,
    host: str,
    port: int,
    database_name: str,
    username: str,
    password: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Probe a connection and return a non-sensitive, bounded result."""
    started = time.perf_counter()
    engine = _engine(
        dialect, host, port, database_name, username, password, options or {}
    )
    try:
        with engine.connect() as conn:
            value = conn.scalar(text("SELECT 1"))
        return {
            "ok": value == 1,
            "status": "ready" if value == 1 else "failed",
            "message": (
                "Connection successful"
                if value == 1
                else "Connection probe returned an unexpected result."
            ),
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception:
        return {
            "ok": False,
            "status": "failed",
            "message": "Unable to connect. Check the host, port, database, credentials, and TLS settings.",
            "latency_ms": None,
        }
    finally:
        engine.dispose()


def inspect_schema(
    *,
    dialect: str,
    host: str,
    port: int,
    database_name: str,
    username: str,
    password: str,
    options: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return trusted table/column/relationship metadata for one database."""
    engine = _engine(
        dialect, host, port, database_name, username, password, options or {}
    )
    try:
        inspector = inspect(engine)
        result: list[dict[str, Any]] = []
        schemas = inspector.get_schema_names()
        if dialect == "mysql":
            schemas = [database_name] if database_name in schemas else []
        else:
            schemas = [
                schema
                for schema in schemas
                if schema not in {"information_schema", "pg_catalog"}
                and not schema.startswith("pg_toast")
                and not schema.startswith("pg_temp_")
            ]
        for schema_name in sorted(schemas):
            for table_name in sorted(inspector.get_table_names(schema=schema_name)):
                columns: list[dict[str, Any]] = []
                for column in inspector.get_columns(table_name, schema=schema_name):
                    columns.append(
                        {
                            "name": column["name"],
                            "type": str(column["type"]),
                            "nullable": bool(column.get("nullable", True)),
                            "default": None,
                        }
                    )
                primary_key = inspector.get_pk_constraint(
                    table_name, schema=schema_name
                )
                primary_columns = set(primary_key.get("constrained_columns") or [])
                for column_info in columns:
                    column_info["primary_key"] = column_info["name"] in primary_columns
                foreign_keys = []
                for fk in inspector.get_foreign_keys(table_name, schema=schema_name):
                    referred_schema = fk.get("referred_schema") or schema_name
                    foreign_keys.append(
                        {
                            "name": fk.get("name"),
                            "columns": fk.get("constrained_columns") or [],
                            "referenced_schema": referred_schema,
                            "referenced_table": fk.get("referred_table"),
                            "referenced_columns": fk.get("referred_columns") or [],
                        }
                    )
                result.append(
                    {
                        "schema": schema_name,
                        "name": table_name,
                        "identity": f"{schema_name}.{table_name}",
                        "columns": columns,
                        "relationships": foreign_keys,
                        "row_count": None,
                        "row_count_estimated": True,
                        "sample": [],
                        "warnings": [
                            "Row count and sample are available after bounded inspection."
                        ],
                    }
                )
        return result
    finally:
        engine.dispose()


def _column_names(names: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    unique: list[str] = []
    mapping: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    for original in names:
        counts[original] = counts.get(original, 0) + 1
        name = original if counts[original] == 1 else f"{original}__{counts[original]}"
        while name in unique:
            counts[original] += 1
            name = f"{original}__{counts[original]}"
        unique.append(name)
        mapping.append({"name": name, "original_name": original})
    return unique, mapping


def _add_mysql_deadline(sql: str, timeout_seconds: int) -> str:
    tree = sqlglot.parse_one(sql, read="mysql")
    hint = exp.Hint(
        expressions=[
            exp.Anonymous(
                this="MAX_EXECUTION_TIME",
                expressions=[exp.Literal.number(timeout_seconds * 1000)],
            )
        ]
    )
    selects = list(tree.find_all(exp.Select))
    if not selects and isinstance(tree, exp.Select):
        selects.append(tree)
    for select_node in selects:
        select_node.set("hint", hint.copy())
    return tree.sql(dialect="mysql")


def _serialize(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime | date | time_of_day):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) > _MAX_SAFE_INTEGER
    ):
        return str(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return str(value)
        return value
    if isinstance(value, dict):
        return {str(key): _serialize(cell) for key, cell in value.items()}
    if isinstance(value, list | tuple):
        return [_serialize(cell) for cell in value]
    if isinstance(value, (str, int, bool)):
        return value
    return str(value)


def execute_query(
    connection: Connection,
    settings: Settings,
    sql: str,
    allowed_tables: set[str],
    max_rows: int,
    timeout_seconds: int,
    control: QueryControl | None = None,
) -> QueryResult:
    """Execute one bounded SELECT in a read-only transaction."""
    if max_rows < 1 or max_rows > 10_000:
        raise ConnectorError("invalid_row_limit", "Row limit must be from 1 to 10000.")
    if timeout_seconds < 1 or timeout_seconds > 30:
        raise ConnectorError(
            "invalid_query_timeout", "Query timeout must be from 1 to 30 seconds."
        )
    normalized = validate_sql(
        sql,
        dialect=connection.dialect,
        allowed_tables=allowed_tables,
        max_rows=max_rows + 1,
    )
    if connection.dialect == "mysql":
        normalized = _add_mysql_deadline(normalized, timeout_seconds)
    password = _credentials(connection, settings)
    engine = _engine(
        connection.dialect,
        connection.host,
        connection.port,
        connection.database_name,
        connection.username,
        password,
        connection.options,
    )
    conn: SqlAlchemyConnection | None = None
    try:
        conn = engine.connect()
        transaction = conn.begin()
        dbapi_connection = conn.connection.driver_connection
        assert dbapi_connection is not None
        if control is not None:
            if connection.dialect == "postgresql":
                control.register(lambda: dbapi_connection.cancel_safe(timeout=1.0))
            else:
                control.register(
                    _mysql_cancel_callback(
                        connection, password, dbapi_connection.thread_id()
                    )
                )
        if connection.dialect == "postgresql":
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql(
                f"SET LOCAL statement_timeout = {int(timeout_seconds * 1000)}"
            )
        else:
            conn.exec_driver_sql(
                f"SET SESSION max_execution_time = {int(timeout_seconds * 1000)}"
            )
            conn.exec_driver_sql("START TRANSACTION READ ONLY")
        if control is not None and control.cancelled:
            raise ConnectorError("query_cancelled", "The query was cancelled.")
        result = conn.exec_driver_sql(normalized)
        original_names = list(result.keys())
        columns, mapping = _column_names(original_names)
        rows: list[dict[str, Any]] = []
        truncated = False
        truncation_reason = None
        result_bytes = len(
            json.dumps(
                {"columns": columns, "column_mapping": mapping},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if result_bytes > settings.max_result_bytes:
            raise ConnectorError(
                "result_metadata_too_large",
                "The result schema exceeds the configured response size limit.",
            )
        for index in range(max_rows + 1):
            if control is not None and control.cancelled:
                raise ConnectorError("query_cancelled", "The query was cancelled.")
            row = result.fetchone()
            if row is None:
                break
            if index == max_rows:
                truncated = True
                truncation_reason = "row_limit"
                break
            serialized_row = {
                column: _serialize(value)
                for column, value in zip(columns, row, strict=True)
            }
            row_bytes = len(
                json.dumps(
                    serialized_row, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            )
            if result_bytes + row_bytes > settings.max_result_bytes:
                if not rows:
                    raise ConnectorError(
                        "result_row_too_large",
                        "A result row exceeds the configured response size limit.",
                    )
                truncated = True
                truncation_reason = "result_byte_limit"
                break
            result_bytes += row_bytes
            rows.append(serialized_row)
        return QueryResult(
            columns=columns,
            rows=rows,
            truncated=truncated,
            row_count=len(rows),
            column_mapping=mapping,
            truncated_reason=truncation_reason,
        )
    except SqlPolicyError:
        raise
    except ConnectorError:
        raise
    except Exception as error:
        message = str(error).lower()
        if control is not None and control.cancelled:
            raise ConnectorError(
                "query_cancelled", "The query was cancelled."
            ) from None
        if (
            "statement timeout" in message
            or "max_execution_time" in message
            or "query execution was interrupted" in message
        ):
            raise ConnectorError(
                "query_timeout", "The database query exceeded its time limit."
            ) from None
        raise ConnectorError(
            "query_failed", "The read query could not be completed."
        ) from None
    finally:
        if control is not None:
            control.clear()
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
            conn.close()
        engine.dispose()


def sample_dataset_rows(
    session: Session,
    dataset: Dataset,
    settings: Settings,
    *,
    offset: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """Return a bounded, precisely serialized page from a persisted DB dataset."""
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or not 0 <= offset <= 1_000_000
    ):
        raise ConnectorError("invalid_offset", "Row offset must be from 0 to 1000000.")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ConnectorError("invalid_row_limit", "Page size must be from 1 to 100.")
    source = session.get(Source, dataset.source_id)
    if source is None or source.kind not in _DEFAULT_PORT:
        raise ConnectorError("invalid_dataset", "Database dataset is unavailable.")
    connection = session.scalar(
        select(Connection).where(Connection.source_id == source.id)
    )
    if connection is None:
        raise ConnectorError(
            "connection_not_found", "Database connection is unavailable."
        )
    schema_name = dataset.details.get("schema")
    table_name = dataset.details.get("name")
    if not isinstance(schema_name, str) or not isinstance(table_name, str):
        raise ConnectorError(
            "invalid_dataset", "Database dataset metadata is incomplete."
        )
    password = _credentials(connection, settings)
    engine = _engine(
        connection.dialect,
        connection.host,
        connection.port,
        connection.database_name,
        connection.username,
        password,
        connection.options,
    )
    try:
        preparer = engine.dialect.identifier_preparer
        qualified = f"{preparer.quote_schema(schema_name)}.{preparer.quote(table_name)}"
        primary_columns = [
            column["name"]
            for column in dataset.details.get("columns", [])
            if isinstance(column, dict)
            and isinstance(column.get("name"), str)
            and column.get("primary_key")
        ]
        ordering = (
            " ORDER BY " + ", ".join(preparer.quote(name) for name in primary_columns)
            if primary_columns
            else ""
        )
        with engine.connect() as conn:
            conn.begin()
            if connection.dialect == "postgresql":
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                conn.exec_driver_sql("SET LOCAL statement_timeout = 30000")
            else:
                conn.exec_driver_sql("SET SESSION max_execution_time = 30000")
                conn.exec_driver_sql("START TRANSACTION READ ONLY")
            total_rows = int(
                conn.exec_driver_sql(f"SELECT COUNT(*) FROM {qualified}").scalar_one()
            )
            result = conn.execute(
                text(
                    f"SELECT * FROM {qualified}{ordering} LIMIT :limit OFFSET :offset"
                ),
                {"limit": limit, "offset": offset},
            )
            original_names = list(result.keys())
            columns, mapping = _column_names(original_names)
            rows: list[dict[str, Any]] = []
            result_bytes = len(
                json.dumps(
                    {"columns": columns, "column_mapping": mapping},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            if result_bytes > settings.max_result_bytes:
                raise ConnectorError(
                    "result_metadata_too_large",
                    "The result schema exceeds the configured response size limit.",
                )
            truncated_reason: str | None = None
            for index in range(limit + 1):
                row = result.fetchone()
                if row is None:
                    break
                if index == limit:
                    truncated_reason = "row_limit"
                    break
                serialized_row = {
                    column: _serialize(value)
                    for column, value in zip(columns, row, strict=True)
                }
                row_bytes = len(
                    json.dumps(
                        serialized_row, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                )
                if result_bytes + row_bytes > settings.max_result_bytes:
                    if not rows:
                        raise ConnectorError(
                            "result_row_too_large",
                            "A result row exceeds the configured response size limit.",
                        )
                    truncated_reason = "result_byte_limit"
                    break
                result_bytes += row_bytes
                rows.append(serialized_row)
        return {
            "columns": columns,
            "rows": rows,
            "offset": offset,
            "limit": limit,
            "total_rows": _serialize(total_rows),
            "truncated": offset + len(rows) < total_rows
            or truncated_reason is not None,
            "column_mapping": mapping,
            "truncated_reason": truncated_reason,
        }
    except ConnectorError:
        raise
    except Exception:
        raise ConnectorError(
            "dataset_read_failed", "Could not read the selected dataset."
        ) from None
    finally:
        engine.dispose()


def _mysql_cancel_callback(
    connection: Connection, password: str, thread_id: int
) -> Callable[[], None]:
    def cancel() -> None:
        options = normalize_options(connection.options)
        kwargs: dict[str, Any] = {
            "host": connection.host,
            "port": connection.port,
            "user": connection.username,
            "password": password,
            "database": connection.database_name,
            "connect_timeout": options["connect_timeout_seconds"],
            "autocommit": True,
        }
        context = _ssl_context(options["ssl_mode"])
        if context is None:
            kwargs["ssl_disabled"] = True
        else:
            kwargs["ssl"] = context
        admin = pymysql.connect(**kwargs)
        try:
            with admin.cursor() as cursor:
                cursor.execute(f"KILL QUERY {int(thread_id)}")
        finally:
            admin.close()

    return cancel
