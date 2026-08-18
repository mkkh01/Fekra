from __future__ import annotations

from fastapi import APIRouter, Request

from app.config.settings import get_settings

router = APIRouter(prefix="/api")


@router.get("/assets")
async def assets(request: Request) -> list[dict]:
    state = request.app.state.runtime
    symbols = list(request.app.state.market.symbols)
    return [state.tickers.get(symbol, {"symbol": symbol, "status": "WAITING"}) for symbol in symbols]


@router.get("/brain/status")
async def brain_status(request: Request) -> dict:
    state = request.app.state.runtime
    return {
        "status": state.brain_status,
        "mode": get_settings().trading_mode,
        "last_cycle": state.cycles[0] if state.cycles else None,
    }


@router.get("/system/health")
async def system_health(request: Request) -> dict:
    return request.app.state.runtime.health()


@router.get("/system/gemini-usage")
async def gemini_usage(request: Request) -> dict:
    return request.app.state.runtime.gemini_usage


@router.post("/system/gemini-probe")
async def gemini_probe(request: Request) -> dict:
    """Probe each configured Gemini key once with a minimal JSON request."""
    return await request.app.state.gemini.probe_all()


@router.get("/cycles")
async def cycles(request: Request) -> list[dict]:
    return list(request.app.state.runtime.cycles)


@router.get("/news")
async def news(request: Request) -> list[dict]:
    return list(request.app.state.runtime.news)


@router.get("/chart/{symbol}")
async def chart(symbol: str, request: Request, interval: str = "5m", limit: int = 100) -> list[dict]:
    return await request.app.state.market.candles(symbol, interval, limit)


@router.get("/open-trades")
async def open_trades(request: Request) -> list[dict]:
    return request.app.state.runtime.positions


@router.post("/brain/reassess/{symbol}")
async def reassess(symbol: str, request: Request) -> dict:
    symbol = symbol.upper()
    state = request.app.state.runtime
    state.brain_status = "RESEARCHING"
    state.event("BRAIN", f"Manual reassessment requested for {symbol}")
    await request.app.state.brain.request(symbol)
    return {"accepted": True, "symbol": symbol, "mode": get_settings().trading_mode}


@router.post("/system/kill")
async def kill_switch(request: Request) -> dict:
    state = request.app.state.runtime
    request.app.state.brain.activate_kill_switch()
    state.event("SYSTEM", "Emergency stop activated; all new Brain cycles are blocked")
    await request.app.state.storage.write_event("SYSTEM", "Emergency stop activated", {"live_execution": False})
    return {"mode": "EMERGENCY_STOP", "live_execution": False, "kill_switch": True}
