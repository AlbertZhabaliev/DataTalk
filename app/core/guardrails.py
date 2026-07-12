from __future__ import annotations

import sqlglot
from sqlglot import exp

# Forbidden statement kinds and expressions for ad-hoc NL->DB queries.
_DENY_STATEMENT_NAMES = {
    "Insert", "Update", "Delete", "Drop", "Alter", "Create",
    "Grant", "Revoke", "Merge", "Truncate", "Attach", "Call", "Command",
}
_DENY_STATEMENTS = tuple(
    getattr(exp, n) for n in _DENY_STATEMENT_NAMES if hasattr(exp, n)
)
_DENY_FUNCS = {"xp_cmdshell", "sys_exec", "utl_http", "utl_file", "dbms_"}
_DENY_TOKEN_PATTERNS = ("into outfile", "into dumpfile", "@@", "pg_sleep")


def assert_read_only(sql: str, dialect: str = "") -> None:
    """Raise ValueError if the SQL is not a safe, single read statement."""
    cleaned = sql.strip().rstrip(";").strip()

    try:
        parsed = sqlglot.parse(cleaned, read=_dialect(dialect))
    except sqlglot.errors.ParseError as e:
        raise ValueError(f"Could not parse SQL: {e}") from e

    statements = [s for s in parsed if s is not None]
    if len(statements) != 1:
        raise ValueError("Only a single statement is allowed.")

    root = statements[0]
    if isinstance(root, exp.Command):
        # Bare commands (e.g. 'SHOW') are allowed only if whitelisted.
        if root.name.lower() not in {"show", "describe", "explain", "with", "select"}:
            raise ValueError("Unsupported command.")
        return

    if any(isinstance(root, t) for t in _DENY_STATEMENTS):
        raise ValueError("Data modification / DDL statements are forbidden.")

    if not isinstance(root, (exp.Select, exp.With)):
        raise ValueError("Only SELECT / WITH queries are allowed.")

    for node in root.walk():
        expr = node.expression
        if isinstance(expr, exp.Anonymous) and expr.name.lower() in _DENY_FUNCS:
            raise ValueError(f"Forbidden function: {expr.name}")
        if isinstance(expr, exp.Func) and any(
            expr.name.lower().startswith(p) for p in _DENY_FUNCS if p.endswith("_")
        ):
            raise ValueError(f"Forbidden package/function: {expr.name}")
        if isinstance(expr, exp.Column) and str(expr.name).lower().startswith("@@"):
            raise ValueError("Server global variables are forbidden.")

    _check_deny_tokens(cleaned)


def _check_deny_tokens(sql: str) -> None:
    low = sql.lower()
    for pat in _DENY_TOKEN_PATTERNS:
        if pat in low:
            raise ValueError(f"Forbidden token in query: {pat!r}")


def _dialect(dialect: str) -> str | None:
    return {
        "clickhouse": "clickhouse",
        "postgres": "postgres",
        "mssql": "tsql",
        "oracle": "oracle",
    }.get(dialect, None)
