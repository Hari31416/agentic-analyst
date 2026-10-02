from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.sources.connections import (
    ConnectorError,
    QueryControl,
    _column_names,
    _add_mysql_deadline,
    _serialize,
    decrypt_credentials,
    encrypt_credentials,
    normalize_options,
)


def test_credentials_are_encrypted_and_require_a_persistent_key():
    key = Fernet.generate_key().decode()
    settings = Settings(_env_file=None, database_encryption_key=key)
    stored = encrypt_credentials("analyst-secret", settings)
    assert stored != "analyst-secret"
    assert decrypt_credentials(stored, settings) == "analyst-secret"
    with pytest.raises(ConnectorError, match="DATABASE_ENCRYPTION_KEY"):
        encrypt_credentials("secret", Settings(_env_file=None))


@pytest.mark.parametrize(
    "options",
    [
        {"ssl_mode": "verify-full", "connect_timeout_seconds": 5},
        {"ssl_mode": "disable", "connect_timeout_seconds": 15},
    ],
)
def test_connection_options_are_whitelisted(options):
    assert normalize_options(options) == options
    with pytest.raises(ConnectorError):
        normalize_options({**options, "init_command": "DROP TABLE users"})
    with pytest.raises(ConnectorError):
        normalize_options({"ssl_mode": "unknown"})
    with pytest.raises(ConnectorError):
        normalize_options({"connect_timeout_seconds": 1000})


def test_serialization_preserves_exact_numbers_and_dates():
    assert _serialize(Decimal("25000.50")) == "25000.50"
    assert _serialize(Decimal("25000.00")) == "25000.00"
    assert _serialize(42) == 42
    assert _serialize(2**53) == str(2**53)
    assert _serialize(date(2026, 10, 2)) == "2026-10-02"
    assert (
        _serialize(datetime(2026, 10, 2, tzinfo=timezone.utc))
        == "2026-10-02T00:00:00+00:00"
    )


def test_duplicate_columns_get_stable_names_and_mapping():
    names, mapping = _column_names(["id", "value", "id", "id__2", "id"])
    assert names == ["id", "value", "id__2", "id__2__2", "id__3"]
    assert mapping[2] == {"name": "id__2", "original_name": "id"}


def test_query_control_cancels_current_and_future_registration():
    control = QueryControl()
    calls = []
    control.register(lambda: calls.append("first"))
    control.cancel()
    control.cancel()
    control.register(lambda: calls.append("late"))
    assert calls == ["first", "late"]


def test_mysql_query_deadline_is_added_as_server_optimizer_hint():
    query = _add_mysql_deadline(
        "WITH selected AS (SELECT id FROM sales) SELECT COUNT(*) FROM selected", 3
    )
    assert "MAX_EXECUTION_TIME(3000)" in query
    assert query.count("MAX_EXECUTION_TIME") == 2
