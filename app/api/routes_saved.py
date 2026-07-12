from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config.connections import registry
from app.core.saved_store import get_saved_store

router = APIRouter(prefix="/api", tags=["saved"])

FULL_MAX_ROWS = 50000


class ChartConfig(BaseModel):
    name: str = "Chart"
    type: str = "bar"           # bar | line | area | pie | scatter | none
    x: str | None = None        # category / x column name
    y: list[str] = Field(default_factory=list)
    pinned: bool = False        # pinned to dashboard


class SavedQuery(BaseModel):
    id: str | None = None
    name: str
    db: str
    question: str = ""
    sql: str
    explanation: str = ""
    charts: list[ChartConfig] = Field(default_factory=list)
    favorite: bool = False
    layout: str = "table"       # table | charts-top | charts-left | charts-right | charts-grid


class RunSQL(BaseModel):
    db: str
    sql: str
    limit: int | None = None


class ExplainRequest(BaseModel):
    db: str
    sql: str
    question: str = ""


def _run(db: str, sql: str, limit: int | None) -> dict[str, Any]:
    executor = registry.get(db)
    cap = min(limit or FULL_MAX_ROWS, FULL_MAX_ROWS)
    res = executor.execute(sql, max_rows=cap)
    return {
        "columns": res.columns,
        "rows": res.rows,
        "row_count": res.row_count,
        "truncated": res.truncated,
        "dialect": res.dialect,
    }


@router.post("/explain")
async def explain_sql(body: ExplainRequest):
    """Use the LLM to explain what a SQL query calculates."""
    try:
        explanation = await _generate_explanation(body.db, body.sql, body.question)
        return {"explanation": explanation}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Explain failed: {e}")


async def _generate_explanation(db: str, sql: str, question: str) -> str:
    from app.core.llm.sql_chain import _build_llm
    from app.core.schema.retriever import SchemaRetriever

    retriever = SchemaRetriever()
    context = retriever.build_context(db, question or sql)

    prompt = (
        "You are a data analyst. Explain the following SQL query in clear, "
        "plain language that a non-technical business user would understand.\n"
        "Describe what the query calculates, what it returns, and the key "
        "business logic (filters, aggregations, joins). Keep it concise.\n\n"
        f"Database schema:\n{context}\n\n"
        f"SQL query:\n{sql}\n\n"
        f"User question: {question}\n\n" if question else ""
        "Explanation:"
    )

    from langchain_core.messages import HumanMessage
    llm = _build_llm()
    resp = await llm.ainvoke([HumanMessage(content=prompt)])
    return getattr(resp, "content", str(resp)).strip()


@router.post("/run")
async def run_sql(body: RunSQL):
    try:
        return _run(body.db, body.sql, body.limit)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Unsafe/invalid query: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Execution failed: {e}")


@router.get("/saved")
async def list_saved():
    return {"queries": get_saved_store().list()}


@router.post("/saved")
async def save_query(q: SavedQuery):
    return get_saved_store().upsert(q.model_dump(exclude_unset=True))


@router.delete("/saved/{qid}")
async def delete_saved(qid: str):
    if not get_saved_store().delete(qid):
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}


@router.post("/saved/{qid}/favorite")
async def toggle_favorite(qid: str, body: dict):
    fav = body.get("favorite", True)
    store = get_saved_store()
    item = store.get(qid)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    item["favorite"] = fav
    store.upsert(item)
    return {"ok": True, "favorite": fav}


@router.post("/saved/{qid}/run")
async def run_saved(qid: str, limit: int | None = None):
    item = get_saved_store().get(qid)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        return _run(item["db"], item["sql"], limit)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Unsafe/invalid query: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Execution failed: {e}")
