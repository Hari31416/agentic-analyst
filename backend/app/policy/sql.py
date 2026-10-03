"""Dialect-aware SQL allowlist for selected source datasets."""

from __future__ import annotations

from collections.abc import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope


class SqlPolicyError(ValueError):
    """Raised when a query falls outside the supported read-only policy."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Unknown functions are rejected. This list intentionally favors portable,
# deterministic scalar/aggregate operations over dialect-specific capabilities.
_SAFE_FUNCTIONS = frozenset(
    {
        "ABS",
        "ACOS",
        "ASIN",
        "ATAN",
        "ATAN2",
        "AVG",
        "CEIL",
        "CEILING",
        "CHAR_LENGTH",
        "CHR",
        "COALESCE",
        "CONCAT",
        "CONCAT_WS",
        "COS",
        "COT",
        "COUNT",
        "DATE",
        "DATE_ADD",
        "DATE_PART",
        "DATE_SUB",
        "DATEDIFF",
        "DAY",
        "DAYOFMONTH",
        "DAYOFWEEK",
        "DAYOFYEAR",
        "DENSE_RANK",
        "EXTRACT",
        "FLOOR",
        "FIRST_VALUE",
        "GREATEST",
        "HOUR",
        "IFNULL",
        "INSTR",
        "JSON_ARRAY",
        "JSON_ARRAY_LENGTH",
        "JSON_CONTAINS",
        "JSON_EXTRACT",
        "JSON_OBJECT",
        "JSON_QUERY",
        "JSON_TYPE",
        "JSON_VALID",
        "JSON_VALUE",
        "LAG",
        "LAST_DAY",
        "LAST_VALUE",
        "LEAD",
        "LEAST",
        "LEFT",
        "LENGTH",
        "LN",
        "LOCATE",
        "LOG",
        "LOG10",
        "LOWER",
        "LPAD",
        "LTRIM",
        "MAX",
        "MIN",
        "MINUTE",
        "MOD",
        "MONTH",
        "NTH_VALUE",
        "NTILE",
        "NULLIF",
        "OCTET_LENGTH",
        "POWER",
        "POW",
        "QUARTER",
        "RANK",
        "REPEAT",
        "REPLACE",
        "REVERSE",
        "RIGHT",
        "ROUND",
        "ROW_NUMBER",
        "RPAD",
        "RTRIM",
        "SIGN",
        "SIN",
        "SOUNDEX",
        "SQRT",
        "STDDEV",
        "STDDEV_POP",
        "STDDEV_SAMP",
        "SUBSTR",
        "SUBSTRING",
        "SUM",
        "TAN",
        "TIMESTAMPDIFF",
        "TIMESTAMP_TRUNC",
        "TRIM",
        "TRUNC",
        "TRUNCATE",
        "UPPER",
        "VARIANCE",
        "VAR_POP",
        "VAR_SAMP",
        "WEEK",
        "YEAR",
    }
)

_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Revoke,
    exp.Command,
    exp.Transaction,
    exp.Set,
    exp.Use,
    exp.Pragma,
    exp.Copy,
    exp.LoadData,
    exp.Kill,
    exp.Analyze,
    exp.Into,
    exp.Lock,
    exp.UDTF,
    exp.Hint,
    exp.JSONTable,
    exp.XMLTable,
)

_SYSTEM_SCHEMAS = frozenset(
    {
        "information_schema",
        "mysql",
        "performance_schema",
        "pg_catalog",
        "pg_toast",
        "sys",
    }
)
_MAX_SQL_CHARACTERS = 20_000
_MAX_AST_NODES = 20_000
_MAX_AST_DEPTH = 100
_MAX_JOINS = 32
_MAX_LITERAL_CHARACTERS = 4_096
_MAX_REPEAT_COUNT = 100_000


def validate_sql(
    sql: str,
    *,
    dialect: str,
    allowed_tables: Iterable[str] | None = None,
    max_rows: int | None = None,
) -> str:
    """Validate one deterministic read query and return normalized SQL.

    ``allowed_tables`` contains trusted selected table identities, either
    ``table`` or ``schema.table``. CTE references are allowed by their declared
    alias; physical table references must match the trusted set when supplied.
    An outer limit bounds returned rows without truncating aggregate inputs.
    """
    source = sql.strip() if isinstance(sql, str) else ""
    if not source:
        raise SqlPolicyError("empty_sql", "SQL query is empty")
    if len(source) > _MAX_SQL_CHARACTERS:
        raise SqlPolicyError("sql_too_large", "SQL query exceeds the size limit")
    # MySQL executes /*! ... */ contents as version-gated SQL. sqlglot treats
    # these as ordinary comments and drops their executable contents.
    if dialect.lower() in {"mysql", "mariadb"} and "/*!" in source:
        raise SqlPolicyError(
            "executable_comment_forbidden", "Executable SQL comments are not allowed"
        )
    if max_rows is not None and max_rows < 1:
        raise ValueError("max_rows must be positive")

    try:
        normalized_dialect = "postgres" if dialect.lower() == "postgresql" else dialect
        statements = sqlglot.parse(source, read=normalized_dialect)
    except (sqlglot.errors.ParseError, ValueError, RecursionError):
        raise SqlPolicyError(
            "sql_parse_error", "SQL query could not be parsed"
        ) from None
    if len(statements) != 1 or statements[0] is None:
        raise SqlPolicyError("multiple_statements", "Only one SQL query is allowed")
    tree = statements[0]

    _validate_tree_bounds(tree)

    if any(next(tree.find_all(node_type), None) for node_type in _FORBIDDEN_NODES):
        raise SqlPolicyError("forbidden_statement", "SQL operation is not allowed")
    if not isinstance(tree, (exp.Select, exp.Union, exp.Except, exp.Intersect)):
        raise SqlPolicyError("not_readonly", "Only SELECT queries and CTEs are allowed")

    _validate_functions(tree)
    _validate_tables(tree, allowed_tables)

    if max_rows is not None:
        existing = tree.args.get("limit")
        existing_count = _literal_limit(existing)
        limit = max_rows if existing_count is None else min(max_rows, existing_count)
        tree.set("limit", exp.Limit(expression=exp.Literal.number(limit)))
    return tree.sql(dialect=normalized_dialect)


def _validate_functions(tree: exp.Expression) -> None:
    for function in tree.find_all(exp.Func):
        if isinstance(function, (exp.And, exp.Or, exp.Case, exp.If)):
            continue
        if isinstance(function, (exp.Cast, exp.TryCast)):
            target = function.args.get("to")
            if (
                not isinstance(target, exp.DataType)
                or target.this == exp.DataType.Type.USERDEFINED
            ):
                raise SqlPolicyError(
                    "cast_type_not_allowed", "Only built-in SQL cast types are allowed"
                )
            continue
        if isinstance(function, exp.Anonymous):
            name = function.name.upper()
            # Qualified calls such as public.count() can resolve to a user's
            # function and shadow the built-in allowlist entry.
            if (
                function.args.get("db")
                or function.args.get("catalog")
                or (
                    isinstance(function.parent, exp.Dot)
                    and function.parent.expression is function
                )
            ):
                raise SqlPolicyError(
                    "qualified_function_forbidden",
                    "Qualified SQL functions are not allowed",
                )
        else:
            name = function.sql_name().upper()  # type: ignore[no-untyped-call]
        if name not in _SAFE_FUNCTIONS:
            raise SqlPolicyError("function_not_allowed", "SQL function is not allowed")
        if name == "REPEAT":
            args = function.args.get("expressions", [])
            if (
                len(args) < 2
                or not isinstance(args[1], exp.Literal)
                or args[1].is_string
            ):
                raise SqlPolicyError(
                    "function_argument_limit", "SQL function argument exceeds the limit"
                )
            try:
                count = int(args[1].this)
            except (TypeError, ValueError):
                count = _MAX_REPEAT_COUNT + 1
            if count < 0 or count > _MAX_REPEAT_COUNT:
                raise SqlPolicyError(
                    "function_argument_limit", "SQL function argument exceeds the limit"
                )


def _validate_tables(
    tree: exp.Expression, allowed_tables: Iterable[str] | None
) -> None:
    allowed = set(allowed_tables) if allowed_tables is not None else None
    cte_table_ids: set[int] = set()
    try:
        for scope in traverse_scope(tree):
            for table in scope.tables:
                source = scope.sources.get(table.alias_or_name)
                if isinstance(source, Scope):
                    cte_table_ids.add(id(table))
    except RecursionError:
        raise SqlPolicyError(
            "query_too_complex", "SQL query structure exceeds the limit"
        ) from None
    for table in tree.find_all(exp.Table):
        name = table.name
        schema = table.db
        catalog = table.catalog
        if (schema or "").lower() in _SYSTEM_SCHEMAS or (
            catalog or ""
        ).lower() in _SYSTEM_SCHEMAS:
            raise SqlPolicyError(
                "system_schema_forbidden", "System schemas are not queryable"
            )
        if id(table) in cte_table_ids:
            continue
        identity = ".".join(part for part in (catalog, schema, name) if part)
        unqualified_match = (
            not schema and not catalog and name in allowed
            if allowed is not None
            else False
        )
        if allowed is not None and identity not in allowed and not unqualified_match:
            raise SqlPolicyError(
                "table_not_selected",
                "Query references a table outside the selected sources",
            )


def _validate_tree_bounds(tree: exp.Expression) -> None:
    nodes = 0
    joins = 0
    stack: list[tuple[exp.Expression, int]] = [(tree, 1)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_AST_NODES or depth > _MAX_AST_DEPTH:
            raise SqlPolicyError(
                "query_too_complex", "SQL query structure exceeds the limit"
            )
        if isinstance(node, exp.Join):
            joins += 1
            if joins > _MAX_JOINS:
                raise SqlPolicyError(
                    "too_many_joins", "SQL query exceeds the join limit"
                )
        if (
            isinstance(node, exp.Literal)
            and node.is_string
            and len(str(node.this)) > _MAX_LITERAL_CHARACTERS
        ):
            raise SqlPolicyError(
                "literal_too_large", "SQL literal exceeds the size limit"
            )
        stack.extend((child, depth + 1) for child in node.iter_expressions())


def _literal_limit(node: exp.Expression | None) -> int | None:
    if node is None:
        return None
    value = node.args.get("expression")
    if not isinstance(value, exp.Literal) or value.is_string:
        return None
    try:
        return int(value.this)
    except (TypeError, ValueError):
        return None
