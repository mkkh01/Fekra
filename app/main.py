from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.brain.gemini import GeminiKeyPool
from app.brain.orchestrator import BrainOrchestrator
from app.config.settings import get_settings
from app.market.service import MarketService
from app.news.service import NewsService
from app.state import RuntimeState
from app.storage.store import StorageManager

logging.basicConfig(level=getattr(logging, get_settings().log_level.upper(), logging.INFO))
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    state = RuntimeState()
    state.supabase_configured = bool(settings.supabase_url and settings.supabase_key)
    state.gemini_configured = bool(settings.gemini_keys)

    storage = StorageManager()
    state.supabase_configured = bool(settings.supabase_url and settings.supabase_key)
    state.redis_connected = bool(settings.redis_url)

    async def check_storage() -> None:
        await storage.check()
        state.supabase_configured = storage.supabase_ok
        state.redis_connected = storage.redis_ok

    storage_check_task = asyncio.create_task(check_storage(), name="storage-health-check")

    market = MarketService(state, storage)
    news = NewsService(state, storage)
    gemini = GeminiKeyPool(state)

    app.state.runtime = state
    app.state.market = market
    app.state.news = news
    brain = BrainOrchestrator(state, gemini, storage)

    app.state.gemini = gemini
    app.state.brain = brain
    app.state.storage = storage

    state.event("SYSTEM", "Fekra Trading Brain started", {"mode": settings.trading_mode, "environment": settings.app_env})
    await storage.write_event("SYSTEM", "Fekra Trading Brain started", {"mode": settings.trading_mode, "environment": settings.app_env})
    await market.start()
    await news.start()
    await brain.start()
    try:
        yield
    finally:
        await brain.stop()
        await news.stop()
        await market.stop()
        storage_check_task.cancel()
        try:
            await storage_check_task
        except asyncio.CancelledError:
            pass
        await storage.close()


app = FastAPI(title="Fekra Trading Brain", version="0.1.0", lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/health")
async def health(request: Request) -> dict:
    return request.app.state.runtime.health()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.websocket("/ws/dashboard")
async def dashboard_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            snapshot = websocket.app.state.runtime.dashboard_snapshot()
            await websocket.send_text(json.dumps(snapshot))
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        logger.info("Dashboard WebSocket disconnected")
    except Exception as exc:
        logger.warning("Dashboard WebSocket closed: %s", exc)
