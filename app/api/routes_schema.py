from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config.connections import registry
from app.core.schema.retriever import SchemaRetriever
from app.models.request import SchemaResponse

router = APIRouter(prefix="/api", tags=["schema"])

_retriever = SchemaRetriever()


@router.get("/databases")
async def list_databases():
    return {"databases": registry.list_names()}


@router.get("/schema/{db}", response_model=SchemaResponse)
async def get_schema(db: str):
    try:
        executor = registry.get(db)
        snap = executor.introspect()
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not read schema: {e}")
    return SchemaResponse(
        db=snap.db_name,
        engine=snap.engine,
        tables=[
            {
                "name": t.name,
                "schema": t.schema,
                "qualified_name": t.qualified_name,
                "columns": [{"name": c.name, "type": c.type} for c in t.columns],
            }
            for t in snap.tables
        ],
    )


@router.get("/preview/{db}/{table}")
async def preview_table(db: str, table: str, limit: int = 100, schema: str = ""):
    try:
        executor = registry.get(db)
        res = executor.preview_table(table, limit=min(limit, 5000), schema=schema)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Preview failed: {e}")
    return {
        "db": db,
        "table": table,
        "columns": res.columns,
        "rows": res.rows,
        "row_count": res.row_count,
        "truncated": res.truncated,
    }
