"""Real read-only connector checks against developer-owned seed databases."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import app.sources.connections as connector_module
from app.config import Settings
from app.db.models import Base, Connection, Dataset, Source, Workspace
from app.policy.sql import SqlPolicyError
from app.sources.connections import (
    ConnectorError,
    decrypt_credentials,
    encrypt_credentials,
    execute_query,
    inspect_schema,
    QueryControl,
    sample_dataset_rows,
)

pytestmark = pytest.mark.integration


def _target(dialect: str) -> tuple[Connection, Settings, str]:
    variable = (
        "TEST_POSTGRES_SOURCE_URL"
        if dialect == "postgresql"
        else "TEST_MYSQL_SOURCE_URL"
    )
    dsn = os.getenv(variable)
    if not dsn:
        pytest.skip(f"{variable} is not configured for the seeded source database")
    url = make_url(dsn)
    password = url.password or ""
    settings_key = Fernet.generate_key().decode()
    settings = Settings(database_encryption_key=SecretStr(settings_key))
    source_id = str(uuid4())
    connection = Connection(
        source_id=source_id,
        dialect=dialect,
        host=url.host or "localhost",
        port=url.port or (5432 if dialect == "postgresql" else 3306),
        database_name=url.database or "analyst_eval",
        username=url.username or "",
        encrypted_credentials=encrypt_credentials(password, settings),
        options={"ssl_mode": "disable", "connect_timeout_seconds": 3},
    )
    return connection, settings, dsn


@pytest.fixture
def app_db_factory():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = "test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    # Force creation in the new schema; public is visible only for pgvector.
    Base.metadata.create_all(engine, checkfirst=False)
    try:
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_live_source_query_is_bounded_exact_and_database_readonly(dialect, monkeypatch):
    connection, settings, dsn = _target(dialect)
    database = connection.database_name
    tables = inspect_schema(
        dialect=dialect,
        host=connection.host,
        port=connection.port,
        database_name=database,
        username=connection.username,
        password=decrypt_credentials(connection.encrypted_credentials, settings),
        options=connection.options,
    )
    fixture_table = next(
        item for item in tables if item["name"] == "synthetic_applications"
    )
    assert fixture_table["columns"]
    assert all(
        item["schema"] not in {"information_schema", "mysql", "pg_catalog", "sys"}
        for item in tables
    )
    allowed = {
        "synthetic_applications",
        f"{database}.synthetic_applications",
        "public.synthetic_applications",
    }
    query = execute_query(
        connection,
        settings,
        "SELECT COUNT(*) AS rows, SUM(grant_amount_inr) AS total FROM synthetic_applications",
        allowed,
        10,
        10,
    )
    assert query.rows == [{"rows": 5, "total": "47000.51"}]
    assert query.truncated is False
    assert query.truncated_reason is None

    page = execute_query(
        connection,
        settings,
        "SELECT application_id FROM synthetic_applications ORDER BY application_id",
        allowed,
        2,
        10,
    )
    assert len(page.rows) == 2
    assert page.truncated is True
    assert page.truncated_reason == "row_limit"

    byte_limited = Settings(
        database_encryption_key=settings.database_encryption_key,
        max_result_bytes=1,
    )
    with pytest.raises(ConnectorError) as oversized:
        execute_query(
            connection,
            byte_limited,
            "SELECT application_id FROM synthetic_applications",
            allowed,
            10,
            10,
        )
    assert oversized.value.code == "result_metadata_too_large"
    row_limited = Settings(
        database_encryption_key=settings.database_encryption_key,
        max_result_bytes=120,
    )
    with pytest.raises(ConnectorError) as oversized_row:
        execute_query(
            connection,
            row_limited,
            "SELECT application_id FROM synthetic_applications",
            allowed,
            10,
            10,
        )
    assert oversized_row.value.code == "result_row_too_large"

    with pytest.raises(SqlPolicyError):
        execute_query(
            connection,
            settings,
            "UPDATE synthetic_applications SET grant_amount_inr = 0",
            allowed,
            10,
            10,
        )

    # Exercise the database's own read-only transaction, independently of the
    # parser gate, using the same permissive developer fixture credentials.
    monkeypatch.setattr(
        connector_module,
        "validate_sql",
        lambda *_args, **_kwargs: "UPDATE synthetic_applications SET grant_amount_inr = 0",
    )
    with pytest.raises(ConnectorError):
        execute_query(connection, settings, "SELECT 1", allowed, 10, 10)
    engine = create_engine(dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            unchanged = conn.exec_driver_sql(
                "SELECT SUM(grant_amount_inr) FROM synthetic_applications"
            ).scalar_one()
        assert Decimal(str(unchanged)) == Decimal("47000.51")
    finally:
        engine.dispose()


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_live_source_duplicate_column_names_are_not_lost(dialect):
    connection, settings, _dsn = _target(dialect)
    result = execute_query(
        connection,
        settings,
        "SELECT application_id AS value, scheme_id AS value FROM synthetic_applications ORDER BY application_id",
        {
            "synthetic_applications",
            f"{connection.database_name}.synthetic_applications",
            "public.synthetic_applications",
        },
        2,
        10,
    )
    assert result.columns == ["value", "value__2"]
    assert result.column_mapping == [
        {"name": "value", "original_name": "value"},
        {"name": "value__2", "original_name": "value"},
    ]
    assert result.rows[0]["value"] == "APP-001"
    assert result.rows[0]["value__2"] == "S1"


def _expensive_read() -> str:
    # Five rows raised to the 12th power forces both engines to do enough real
    # work for deadline/cancel checks, while remaining strictly read-only.
    aliases = [f"synthetic_applications AS a{index}" for index in range(12)]
    joins = " CROSS JOIN ".join(aliases)
    terms = " + ".join(f"CHAR_LENGTH(a{index}.application_id)" for index in range(12))
    return f"SELECT SUM({terms}) FROM {joins}"


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_live_source_statement_deadline_is_enforced(dialect):
    connection, settings, _dsn = _target(dialect)
    started = time.monotonic()
    with pytest.raises(ConnectorError) as timed_out:
        execute_query(
            connection,
            settings,
            _expensive_read(),
            {
                "synthetic_applications",
                f"{connection.database_name}.synthetic_applications",
                "public.synthetic_applications",
            },
            10,
            1,
        )
    elapsed = time.monotonic() - started
    assert timed_out.value.code == "query_timeout"
    assert elapsed < 8


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_live_source_query_control_cancels_running_statement(dialect):
    connection, settings, _dsn = _target(dialect)
    control = QueryControl()
    allowed = {
        "synthetic_applications",
        f"{connection.database_name}.synthetic_applications",
        "public.synthetic_applications",
    }
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            execute_query,
            connection,
            settings,
            _expensive_read(),
            allowed,
            10,
            15,
            control,
        )
        # The query has to enter the database before the live cancel callback
        # is useful. Its workload is intentionally far longer than this delay.
        time.sleep(0.35)
        control.cancel()
        with pytest.raises(ConnectorError) as cancelled:
            future.result(timeout=8)
    assert cancelled.value.code == "query_cancelled"


@pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
def test_live_source_dataset_page_uses_quoted_metadata(app_db_factory, dialect):
    external, settings, _dsn = _target(dialect)
    schema_name = "public" if dialect == "postgresql" else external.database_name
    with app_db_factory() as session, session.begin():
        workspace = Workspace(label="connector row sample")
        session.add(workspace)
        session.flush()
        source = Source(
            workspace_id=workspace.id,
            kind=dialect,
            display_name="Synthetic fixture",
            state="ready",
            version=1,
            schema_version="schema-v1",
            details={},
        )
        session.add(source)
        session.flush()
        external.source_id = source.id
        session.add(external)
        dataset = Dataset(
            source_id=source.id,
            source_version=1,
            identity=f"{schema_name}.synthetic_applications",
            schema_version="dataset-v1",
            details={
                "schema": schema_name,
                "name": "synthetic_applications",
                "columns": [{"name": "application_id", "primary_key": True}],
            },
            designation="original",
            lineage=[],
        )
        session.add(dataset)
        session.flush()
        result = sample_dataset_rows(session, dataset, settings, offset=1, limit=2)
    assert result["offset"] == 1
    assert result["limit"] == 2
    assert result["total_rows"] == 5
    assert result["truncated"] is True
    assert result["rows"][0]["application_id"] == "APP-002"
    assert result["rows"][0]["grant_amount_inr"] == "15000.00"
