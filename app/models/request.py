from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class OutputFormat(str, Enum):
    table = "table"
    dashboard = "dashboard"


class QueryRequest(BaseModel):
    db: str = Field(..., description="Configured database name")
    question: str = Field(..., description="Natural-language question")
    format: OutputFormat = OutputFormat.table
    max_rows: int | None = Field(None, ge=1, le=5000)


class QueryResponse(BaseModel):
    db: str
    question: str
    sql: str
    dialect: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    chart: str | None = None
    report: str | None = None
    dashboard_url: str | None = None


class SchemaResponse(BaseModel):
    db: str
    engine: str
    tables: list[dict[str, Any]]
