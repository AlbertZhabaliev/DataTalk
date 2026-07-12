from __future__ import annotations

import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect, text, types
from sqlalchemy.engine import Engine

from app.config.settings import DbConfig
from app.core.executor.engine import build_engine
from app.core.guardrails import assert_read_only

# Register SQL Server types that SQLAlchemy doesn't recognise natively.
# Without this every introspection emits a noisy SAWarning and the column
# appears as "UNKNOWN" instead of a usable type name.
try:
    from sqlalchemy.dialects.mssql import MSSQLDialect
    for _t in ("hierarchyid", "geography", "geometry", "sql_variant"):
        MSSQLDialect.ischema_names.setdefault(_t, types.String)
except ImportError:
    pass
from app.core.schema.models import (
    ColumnMeta,
    ForeignKeyMeta,
    SchemaSnapshot,
    TableMeta,
)


# Cache introspected schemas per database name so we don't hit the catalog
# (and emit SAWarnings) on every Browse / Ask / schema-retrieval call.
_INTROSPECT_CACHE: dict[str, tuple[float, "SchemaSnapshot"]] = {}
_INTROSPECT_TTL = 600  # seconds


# Schemas to hide during introspection (system / role schemas).
SYSTEM_SCHEMAS: dict[str, set[str]] = {
    "postgres": {"pg_catalog", "information_schema"},
    "mssql": {
        "sys", "INFORMATION_SCHEMA", "guest",
        "db_owner", "db_accessadmin", "db_securityadmin", "db_ddladmin",
        "db_backupoperator", "db_datareader", "db_datawriter",
        "db_denydatareader", "db_denydatawriter",
    },
    "oracle": set(),
    "clickhouse": {"system", "INFORMATION_SCHEMA", "information_schema"},
}


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool = False
    dialect: str = ""


def _scalar(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return str(v)


class SqlExecutor:
    """Read-only, guarded, SQLAlchemy-backed bridge to any supported engine."""

    def __init__(self, cfg: DbConfig) -> None:
        self.cfg = cfg
        self._engine: Engine | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = build_engine(self.cfg)
        return self._engine

    # ---- schema discovery -------------------------------------------------
    def introspect(self) -> SchemaSnapshot:
        key = self.cfg.name
        cached = _INTROSPECT_CACHE.get(key)
        if cached is not None and (_time.time() - cached[0]) < _INTROSPECT_TTL:
            return cached[1]
        snapshot = self._introspect()
        _INTROSPECT_CACHE[key] = (_time.time(), snapshot)
        return snapshot

    def _introspect(self) -> SchemaSnapshot:
        insp = inspect(self.engine)
        sysset = SYSTEM_SCHEMAS.get(self.cfg.engine, set())

        if self.cfg.engine == "oracle":
            schemas = [insp.default_schema_name]
        else:
            schemas = [s for s in insp.get_schema_names() if s not in sysset]

        tables: list[TableMeta] = []
        for schema in schemas:
            names = list(insp.get_table_names(schema=schema))
            try:
                names += list(insp.get_view_names(schema=schema))
            except Exception:
                pass
            for tname in names:
                try:
                    raw_cols = insp.get_columns(tname, schema=schema)
                except Exception:
                    continue
                cols = [
                    ColumnMeta(
                        name=c["name"],
                        type=str(c["type"]),
                        description=(c.get("comment") or ""),
                    )
                    for c in raw_cols
                ]
                try:
                    tcomment = insp.get_table_comment(tname, schema=schema).get("text") or ""
                except Exception:
                    tcomment = ""
                try:
                    pk = list(
                        insp.get_pk_constraint(tname, schema=schema).get(
                            "constrained_columns"
                        )
                        or []
                    )
                except Exception:
                    pk = []
                fks: list[ForeignKeyMeta] = []
                try:
                    for fk in insp.get_foreign_keys(tname, schema=schema):
                        cc = fk.get("constrained_columns") or []
                        rc = fk.get("referred_columns") or []
                        if not cc or not fk.get("referred_table"):
                            continue
                        fks.append(
                            ForeignKeyMeta(
                                columns=list(cc),
                                ref_table=fk["referred_table"],
                                ref_columns=list(rc),
                                ref_schema=fk.get("referred_schema") or "",
                            )
                        )
                except Exception:
                    pass
                tables.append(
                    TableMeta(
                        name=tname,
                        schema=schema or "",
                        columns=cols,
                        description=tcomment,
                        primary_key=pk,
                        foreign_keys=fks,
                    )
                )

        self._augment_comments(tables)
        return SchemaSnapshot(
            db_name=self.cfg.name, engine=self.cfg.engine, tables=tables
        )

    def _augment_comments(self, tables: list[TableMeta]) -> None:
        """SQL Server stores comments as MS_Description extended properties,
        which the SQLAlchemy inspector does not surface — fetch them directly."""
        if self.cfg.engine != "mssql":
            return
        q = text(
            "SELECT s.name AS sch, t.name AS tbl, c.name AS col, "
            "CAST(ep.value AS NVARCHAR(MAX)) AS descr "
            "FROM sys.extended_properties ep "
            "JOIN sys.tables t ON ep.major_id = t.object_id "
            "JOIN sys.schemas s ON t.schema_id = s.schema_id "
            "LEFT JOIN sys.columns c "
            "  ON ep.major_id = c.object_id AND ep.minor_id = c.column_id "
            "WHERE ep.name = 'MS_Description' AND ep.class = 1"
        )
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(q).fetchall()
        except Exception:
            return
        idx = {(t.schema, t.name): t for t in tables}
        for sch, tbl, col, descr in rows:
            t = idx.get((sch, tbl))
            if not t or not descr:
                continue
            if col is None:
                t.description = descr
            else:
                for cm in t.columns:
                    if cm.name == col:
                        cm.description = descr
                        break

    # ---- query execution --------------------------------------------------
    def execute(self, sql: str, max_rows: int | None = None) -> QueryResult:
        assert_read_only(sql, self.cfg.engine)
        limit = max_rows or self.cfg.max_rows or 1000
        return self._run(sql, limit)

    def preview_table(self, table: str, limit: int = 100, schema: str = "") -> QueryResult:
        if not schema and "." in table:
            schema, _, table = table.rpartition(".")
        ident = self._qualify(schema, table)
        e = self.cfg.engine
        if e == "mssql":
            base = f"SELECT TOP {int(limit)} {{cols}} FROM {ident}"
        elif e == "oracle":
            base = f"SELECT {{cols}} FROM {ident} FETCH FIRST {int(limit)} ROWS ONLY"
        else:
            base = f"SELECT {{cols}} FROM {ident} LIMIT {int(limit)}"

        cols = self._preview_columns(schema, table) if e == "mssql" else None
        sql = base.format(cols=", ".join(cols) if cols else "*")
        try:
            return self._run(sql, limit)
        except Exception as exc:  # unsupported ODBC type -> cast everything to text
            if e == "mssql" and ("not yet supported" in str(exc) or "ODBC SQL type" in str(exc)):
                all_cols = self._preview_columns(schema, table, force_cast=True)
                if all_cols:
                    sql = base.format(cols=", ".join(all_cols))
                    return self._run(sql, limit)
            raise

    _UNSUPPORTED_TYPES = (
        "geography", "geometry", "hierarchyid", "datetimeoffset", "xml", "udt",
    )

    def _preview_columns(self, schema: str, table: str, force_cast: bool = False):
        try:
            snap = self.introspect()
        except Exception:
            return None
        t = next(
            (t for t in snap.tables if t.name == table and (t.schema or "") == (schema or "")),
            None,
        )
        if t is None:
            return None
        prep = self.engine.dialect.identifier_preparer
        parts = []
        for c in t.columns:
            name = prep.quote(c.name)
            tl = (c.type or "").lower()
            if force_cast or any(u in tl for u in self._UNSUPPORTED_TYPES):
                parts.append(f"CAST({name} AS NVARCHAR(MAX)) AS {name}")
            else:
                parts.append(name)
        return parts

    def _run(self, sql: str, limit: int) -> QueryResult:
        # exec_driver_sql: pass raw SQL straight to the DBAPI, so ':' / '%'
        # in the LLM's SQL are never mistaken for bind parameters.
        with self.engine.connect() as conn:
            res = conn.exec_driver_sql(sql)
            if not res.returns_rows:
                return QueryResult(columns=[], rows=[], row_count=0, dialect=self.cfg.engine)
            cols = list(res.keys())
            fetched = res.fetchmany(limit + 1)
        rows = [[_scalar(v) for v in r] for r in fetched]
        truncated = len(rows) > limit
        if truncated:
            rows = rows[:limit]
        return QueryResult(
            columns=cols,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            dialect=self.cfg.engine,
        )

    def _qualify(self, schema: str, table: str) -> str:
        prep = self.engine.dialect.identifier_preparer
        if schema:
            return f"{prep.quote(schema)}.{prep.quote(table)}"
        return prep.quote(table)


# Backwards-compatible alias (older imports referenced BaseExecutor).
BaseExecutor = SqlExecutor
