import pytest
import sqlglot
from sqlglot import exp

from app.policy.sql import SqlPolicyError, validate_sql


@pytest.mark.parametrize(
    ("dialect", "query", "tables"),
    [
        ("postgres", "SELECT COUNT(*) AS n FROM public.sales", {"public.sales"}),
        ("mysql", "SELECT SUM(amount) FROM sales", {"sales"}),
        (
            "duckdb",
            "WITH chosen AS (SELECT amount FROM data_abc123) SELECT AVG(amount) FROM chosen",
            {"data_abc123"},
        ),
    ],
)
def test_accepts_selected_read_queries(dialect, query, tables):
    normalized = validate_sql(query, dialect=dialect, allowed_tables=tables)
    assert isinstance(sqlglot.parse_one(normalized, read=dialect), exp.Expression)


@pytest.mark.parametrize(
    ("dialect", "query"),
    [
        ("postgres", "INSERT INTO sales VALUES (1)"),
        (
            "postgres",
            "WITH changed AS (DELETE FROM sales RETURNING *) SELECT * FROM changed",
        ),
        ("postgres", "SELECT * FROM sales; DELETE FROM sales"),
        ("mysql", "SELECT * FROM sales INTO OUTFILE '/tmp/out.csv'"),
        ("postgres", "COPY sales TO '/tmp/out.csv'"),
        ("postgres", "SELECT pg_read_file('/etc/passwd')"),
        ("postgres", "SELECT pg_sleep(10)"),
        ("mysql", "SELECT SLEEP(10)"),
        ("mysql", "SELECT LOAD_FILE('/etc/passwd')"),
        ("mysql", "SELECT /*+ MAX_EXECUTION_TIME(0) */ * FROM sales"),
        (
            "mysql",
            "SELECT * FROM JSON_TABLE('[1]', '$[*]' COLUMNS(value INT PATH '$')) AS j",
        ),
        ("mysql", "SELECT /*+ MAX_EXECUTION_TIME(0) */ * FROM sales"),
        (
            "mysql",
            "SELECT * FROM JSON_TABLE('[1]', '$[*]' COLUMNS(value INT PATH '$')) AS j",
        ),
        ("duckdb", "SELECT * FROM read_csv('/tmp/private.csv')"),
        ("postgres", "SELECT untrusted_extension_function(id) FROM sales"),
        ("postgres", "SELECT * FROM pg_catalog.pg_tables"),
        ("postgres", "SELECT * FROM other_schema.sales"),
        ("postgres", "SELECT * FROM sales FOR UPDATE"),
        ("postgres", "SELECT app.count(*) FROM sales"),
        ("postgres", "SELECT * FROM pg_catalog.pg_tables"),
        ("mysql", "SELECT /*!50000 SLEEP(10) */ 1"),
        ("mysql", "SELECT app.count(*) FROM sales"),
        ("mysql", "SELECT * FROM information_schema.tables"),
    ],
)
def test_rejects_writes_unsafe_functions_and_unselected_tables(dialect, query):
    with pytest.raises(SqlPolicyError):
        validate_sql(query, dialect=dialect, allowed_tables={"sales"})


def test_rejects_non_select_roots_and_unknown_udfs():
    for query in (
        "SHOW TABLES",
        "CALL dangerous()",
        "VALUES (1)",
        "SELECT custom_fn(1)",
    ):
        with pytest.raises(SqlPolicyError):
            validate_sql(query, dialect="postgres", allowed_tables=set())


def test_outer_limit_does_not_truncate_aggregate_inputs():
    query = validate_sql(
        "SELECT customer_id, SUM(amount) total FROM sales GROUP BY customer_id ORDER BY total DESC",
        dialect="postgres",
        allowed_tables={"sales"},
        max_rows=7,
    )
    tree = sqlglot.parse_one(query, read="postgres")
    assert tree.args["limit"].expression.this == "7"
    assert tree.find(exp.Sum) is not None
    assert tree.args["group"] is not None


def test_server_limit_is_reduced_not_increased():
    query = validate_sql(
        "SELECT * FROM sales LIMIT 3",
        dialect="mysql",
        allowed_tables={"sales"},
        max_rows=10,
    )
    assert sqlglot.parse_one(query, read="mysql").args["limit"].expression.this == "3"


def test_bad_deadline_limit_is_rejected():
    with pytest.raises(ValueError):
        validate_sql("SELECT 1", dialect="postgres", max_rows=0)


def test_oversized_sql_is_rejected_before_parsing():
    with pytest.raises(SqlPolicyError) as error:
        validate_sql("SELECT " + ("a" * 20_001), dialect="postgres")
    assert error.value.code == "sql_too_large"


def test_file_aggregate_with_boolean_predicates_and_decimal_casts():
    sql = "SELECT COUNT(*), SUM(CAST(grant_amount_inr AS DECIMAL(12,2))) FROM data_a WHERE scheme_id='S1' AND scheme_status='active' AND CAST(annual_income_inr AS DECIMAL(12,2)) <= 200000.00"
    assert "DECIMAL" in validate_sql(sql, dialect="duckdb", allowed_tables={"data_a"})


def test_custom_cast_types_are_rejected():
    with pytest.raises(SqlPolicyError):
        validate_sql("SELECT CAST(1 AS custom_type)", dialect="postgresql")


@pytest.mark.parametrize(
    ("dialect", "query"),
    [
        (
            "postgres",
            "WITH chosen AS (SELECT * FROM sales), hidden AS (SELECT * FROM secret) "
            "SELECT * FROM chosen",
        ),
        (
            "mysql",
            "WITH chosen AS (SELECT * FROM sales), hidden AS (SELECT * FROM secret) "
            "SELECT * FROM chosen",
        ),
    ],
)
def test_cte_aliases_do_not_whitelist_other_physical_tables(dialect, query):
    with pytest.raises(SqlPolicyError) as error:
        validate_sql(query, dialect=dialect, allowed_tables={"sales"})
    assert error.value.code == "table_not_selected"
    assert "secret" not in str(error.value)


def test_nested_cte_resolution_and_shadowing_are_scope_aware():
    accepted = "WITH chosen AS (SELECT * FROM sales) SELECT * FROM chosen"
    validate_sql(accepted, dialect="postgres", allowed_tables={"sales"})
    rejected = (
        "WITH chosen AS (SELECT * FROM sales) "
        "SELECT * FROM (WITH chosen AS (SELECT * FROM secret) SELECT * FROM chosen) nested"
    )
    with pytest.raises(SqlPolicyError) as error:
        validate_sql(rejected, dialect="postgres", allowed_tables={"sales"})
    assert error.value.code == "table_not_selected"


def test_query_ast_resource_bounds_are_enforced():
    with pytest.raises(SqlPolicyError) as error:
        validate_sql("SELECT " + "(" * 110 + "1" + ")" * 110, dialect="postgres")
    assert error.value.code in {"sql_parse_error", "query_too_complex"}
    joins = "SELECT * FROM t0 " + " ".join(
        f"JOIN t{i} ON t{i - 1}.id = t{i}.id" for i in range(1, 34)
    )
    with pytest.raises(SqlPolicyError) as error:
        validate_sql(joins, dialect="postgres")
    assert error.value.code == "too_many_joins"


def test_sql_policy_errors_do_not_include_function_names_or_literals():
    with pytest.raises(SqlPolicyError) as error:
        validate_sql("SELECT private_secret_fn('top-secret')", dialect="postgres")
    assert error.value.code == "function_not_allowed"
    assert "private_secret_fn" not in str(error.value)
    assert "top-secret" not in str(error.value)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT REPEAT('x', 100000001)",
        "SELECT REPEAT(value, repeat_count) FROM sales",
    ],
)
def test_repeat_function_has_a_bounded_literal_count(query):
    with pytest.raises(SqlPolicyError) as error:
        validate_sql(query, dialect="mysql", allowed_tables={"sales"})
    assert error.value.code == "function_argument_limit"
