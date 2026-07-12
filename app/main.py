from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes_config import router as config_router
from app.api.routes_query import router as query_router
from app.api.routes_saved import router as saved_router
from app.api.routes_schema import router as schema_router
from app.api.routes_voice import router as voice_router
from app.config.connections import registry  # noqa: F401
from app.config.settings import get_settings
from app.frontend import init_frontend

app = FastAPI(title=get_settings().app_name)
app.include_router(query_router)
app.include_router(schema_router)
app.include_router(voice_router)
app.include_router(config_router)
app.include_router(saved_router)

Path("static/dashboards").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
async def health():
    return {"status": "ok", "databases": registry.list_names()}


@app.on_event("startup")
async def warmup():
    """Pre-warm schema cache so the first user interaction is fast."""
    import asyncio
    from app.config.connections import registry as _reg

    async def _warm_one(name):
        try:
            ex = _reg.get(name)
            snap = await asyncio.to_thread(ex.introspect)
            print(f"[warmup] {name}: {len(snap.tables)} tables cached")
        except Exception as exc:
            print(f"[warmup] {name}: skipped ({exc})")

    await asyncio.gather(*[_warm_one(n) for n in _reg.list_names()], return_exceptions=True)


init_frontend(app)
