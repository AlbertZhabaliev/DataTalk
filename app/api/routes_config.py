from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.config.connections import registry
from app.core.config_store import get_config_store

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("")
async def get_config() -> dict[str, Any]:
    store = get_config_store()
    return {
        "llm": store.get_llm(),
        "databases": store.get_databases(),
        "glossary": {"databases": store.get_glossary()},
        "defaults": store.get_defaults(),
    }


@router.put("/llm")
async def put_llm(payload: dict[str, Any] = Body(...)):
    get_config_store().set_llm(payload)
    return {"ok": True}


@router.put("/databases")
async def put_databases(payload: list[dict[str, Any]] = Body(...)):
    # basic validation
    names = [d.get("name") for d in payload]
    if any(not n for n in names) or len(names) != len(set(names)):
        raise HTTPException(400, "Each database needs a unique 'name'.")
    store = get_config_store()
    store.set_databases(payload)
    registry.reload()  # rebuild executors
    return {"ok": True, "databases": registry.list_names()}


@router.post("/databases/test")
async def test_database(payload: dict[str, Any] = Body(...)):
    from app.config.settings import DbConfig
    try:
        cfg = DbConfig(**payload)
    except Exception as e:
        raise HTTPException(400, f"Invalid config: {e}")
    try:
        from app.config.connections import _build_executor
        ex = _build_executor(cfg)
        ex.introspect()
        return {"ok": True, "tables": ex.introspect().table_names()[:20]}
    except Exception as e:
        raise HTTPException(502, f"Connection failed: {e}")


@router.put("/glossary")
async def put_glossary(payload: dict[str, Any] = Body(...)):
    # payload is the {databases: {...}} structure
    dbs = payload.get("databases", payload)
    get_config_store().set_glossary(dbs)
    return {"ok": True}


@router.post("/llm/pull")
async def pull_model(payload: dict[str, Any] = Body(...)):
    from app.core.llm.ollama_utils import pull_model as _pull
    model = payload.get("model")
    if not model:
        raise HTTPException(400, "model is required")
    try:
        out = _pull(model)
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "output": out}


@router.post("/llm/test")
async def test_llm(payload: dict[str, Any] = Body(default={})):
    """Send a trivial prompt to verify the configured/overridden LLM works."""
    from app.core.llm.sql_chain import test_llm_config
    try:
        reply = await test_llm_config(payload or None)
    except Exception as e:
        raise HTTPException(502, f"AI test failed: {e}")
    return {"ok": True, "reply": reply}


@router.get("/llm/default-prompt")
async def default_prompt():
    from app.core.llm.sql_chain import DEFAULT_SYSTEM_PROMPT
    return {"system_prompt": DEFAULT_SYSTEM_PROMPT}
