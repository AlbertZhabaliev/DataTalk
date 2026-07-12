from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.pipeline import run_query
from app.models.request import OutputFormat, QueryRequest, QueryResponse

router = APIRouter(prefix="/api", tags=["query"])


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    try:
        return await run_query(req)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:  # guardrail / parse rejection
        raise HTTPException(status_code=400, detail=f"Unsafe/invalid query: {e}")
    except Exception as e:  # DB / LLM errors
        raise HTTPException(status_code=502, detail=f"Execution failed: {e}")
