from __future__ import annotations

import uuid
from pathlib import Path

from app.config.connections import registry
from app.core.executor.base import QueryResult
from app.core.llm.report_gen import ReportGenerator
from app.core.llm.sql_chain import SQLGenerator
from app.core.results.dashboard import build_dashboard
from app.core.schema.retriever import SchemaRetriever
from app.models.request import OutputFormat, QueryRequest

_STATIC_DIR = Path("static/dashboards")
_retriever = SchemaRetriever()
_generator = SQLGenerator()
_reporter = ReportGenerator()

# How many times to feed a DB error back to the LLM to self-correct.
_MAX_REPAIR_ATTEMPTS = 2


def _sample(result: QueryResult, n: int = 5) -> str:
    head = result.rows[:n]
    return "\n".join(
        " | ".join(str(v) for v in r) for r in head
    ) or "(no rows)"


async def _execute_with_repair(
    executor, question: str, context: str, sql: str
) -> tuple[QueryResult, str]:
    """Run the SQL; on execution error, ask the LLM to fix it and retry."""
    last_err: Exception | None = None
    for attempt in range(_MAX_REPAIR_ATTEMPTS + 1):
        try:
            return executor.execute(sql), sql
        except Exception as e:  # noqa: BLE001 - surface any DB/driver error to LLM
            last_err = e
            if attempt >= _MAX_REPAIR_ATTEMPTS:
                break
            sql = await _generator.repair(
                question, context, executor.cfg.engine, sql, str(e)
            )
    raise last_err  # type: ignore[misc]


async def run_query(req: QueryRequest) -> dict:
    executor = registry.get(req.db)
    context = _retriever.build_context(req.db, req.question)

    meta = await _generator.generate_full(req.question, context, executor.cfg.engine)
    sql = meta["sql"]

    # allow per-request row cap override (never above configured max)
    cfg_max = executor.cfg.max_rows or 1000
    if req.max_rows:
        executor.cfg.max_rows = min(req.max_rows, cfg_max)

    result, sql = await _execute_with_repair(
        executor, req.question, context, sql
    )

    chart = meta.get("chart")
    report = meta.get("report")
    if chart is None or report is None:
        sample = _sample(result)
        if report is None:
            report = await _reporter.report(req.question, sql, sample)
        if chart is None:
            chart = await _reporter.chart(req.question, sql, result.columns)

    dashboard_url = None
    if req.format == OutputFormat.dashboard:
        _STATIC_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4().hex}.html"
        (_STATIC_DIR / fname).write_text(
            build_dashboard(result, title=req.question, report=report,
                            chart_type=chart),
            encoding="utf-8",
        )
        dashboard_url = f"/static/dashboards/{fname}"

    return {
        "db": req.db,
        "question": req.question,
        "sql": sql,
        "dialect": result.dialect,
        "columns": result.columns,
        "rows": result.rows,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "chart": chart,
        "report": report,
        "dashboard_url": dashboard_url,
    }
