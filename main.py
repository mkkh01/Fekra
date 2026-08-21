from __future__ import annotations
import asyncio, logging, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from app.config import get_settings
from app.data.market import MarketData
from app.analysis.engine import analyze
from app.analysis.backtest import run_backtest
from app.storage.store import Store
from app.notifications import PushNotifier
from app.telegram import TelegramBotController, TelegramNotifier

settings = get_settings()
market = MarketData(settings.binance_rest_url, settings.binance_ws_url, settings.symbol_list, settings.default_interval, ["15m", "1h", "4h"])
store = Store(settings.database_path, settings.supabase_http_url, settings.supabase_auth_keys, settings.redis_url, settings.postgres_dsn)
push_notifier = PushNotifier(store, settings.vapid_private_key, settings.vapid_subject)
telegram_notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
log = logging.getLogger("weeg.auto_signals")
push_log = logging.getLogger("weeg.push")
AUTO_SCAN_SECONDS = 60
STORAGE_RETRY_SECONDS = 30
EXIT_SCAN_SECONDS = 5
cycle_state = {
    "status": "STARTING",
    "started_at": None,
    "finished_at": None,
    "next_run_at": None,
    "scanned_symbols": 0,
    "ready_signals": 0,
    "ready_symbols": [],
    "saved_trades": 0,
    "last_saved_symbols": [],
    "last_error": None,
    "completed_cycles": 0,
    "run_id": 0,
    "last_run_duration_seconds": None,
}
telegram_bot = TelegramBotController(telegram_notifier, store, market, settings, cycle_state)

class TradeInput(BaseModel):
    symbol: str
    direction: str
    timeframe: str = "15m"
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    confidence: float = Field(ge=0, le=100)
    risk_reward: float
    regime: str = "TRANSITION"
    structure_state: str = "NEUTRAL"
    liquidity_state: str = "UNKNOWN"
    fvg_state: str = "UNKNOWN"
    volume_state: str = "UNKNOWN"
    momentum_state: str = "UNKNOWN"

class SettingsInput(BaseModel):
    symbols: list[str] | None = None
    confidence_threshold: int | None = None
    minimum_rr: float | None = None
    risk_per_trade: float | None = None


class PushSubscriptionInput(BaseModel):
    endpoint: str = Field(min_length=20, max_length=4096)
    p256dh: str = Field(min_length=20, max_length=512)
    auth: str = Field(min_length=8, max_length=256)
    expiration_time: float | None = None
    user_agent: str | None = Field(default=None, max_length=512)

MTF_INTERVALS = ("4h", "1h", "15m")


def _opposite(direction: str) -> str:
    return "SHORT" if direction == "LONG" else "LONG"


async def _analyze_mtf(symbol: str) -> dict:
    rows_4h, rows_1h, rows_15m = await asyncio.gather(
        market.ensure_history(symbol, "4h"),
        market.ensure_history(symbol, "1h"),
        market.ensure_history(symbol, "15m"),
    )
    results = {
        "4h": analyze(symbol, rows_4h, "4h", settings.confidence_threshold, settings.minimum_rr),
        "1h": analyze(symbol, rows_1h, "1h", settings.confidence_threshold, settings.minimum_rr),
        "15m": analyze(symbol, rows_15m, "15m", settings.confidence_threshold, settings.minimum_rr),
    }
    entry = results["15m"]
    entry_signal = entry.get("signal")
    timeframe_signals = {interval: results[interval].get("signal") for interval in MTF_INTERVALS}
    timeframe_ready = {interval: bool(results[interval].get("ready")) for interval in MTF_INTERVALS}
    vetoes = []
    if entry_signal not in ("LONG", "SHORT"):
        vetoes.append("إشارة 15m ليست LONG أو SHORT")
    else:
        for interval in MTF_INTERVALS:
            signal = timeframe_signals[interval]
            if signal != entry_signal:
                vetoes.append(f"عدم توافق {interval}: {signal} مقابل {entry_signal}")
            if not timeframe_ready[interval]:
                vetoes.append(f"الفريم {interval} غير جاهز")

    fully_aligned = (
        entry_signal in ("LONG", "SHORT")
        and all(timeframe_signals[interval] == entry_signal for interval in MTF_INTERVALS)
        and all(timeframe_ready.values())
    )
    entry = {
        **entry,
        "timeframes": {
            interval: {key: result.get(key) for key in ("signal", "bias", "confidence", "structure", "regime", "ready")}
            for interval, result in results.items()
        },
        "mtf_alignment": "ALIGNED" if fully_aligned else "VETO",
        "mtf_vetoes": vetoes,
    }
    if not fully_aligned:
        entry["signal"] = "NO TRADE"
        entry["ready"] = False
        entry["reasons"] = [
            *entry.get("reasons", []),
            *vetoes,
            "تم رفض الإشارة: يجب تطابق اتجاه 4h و1h و15m وجاهزية الفريمات الثلاثة",
        ]
    return entry


async def _scan_and_store_auto_signals() -> list[dict]:
    saved = []
    ready_signals = 0
    for symbol in settings.symbol_list:
        try:
            result = await _analyze_mtf(symbol)
            if not result.get("ready") or result.get("signal") not in ("LONG", "SHORT"):
                continue
            ready_signals += 1
            cycle_state["ready_signals"] = ready_signals
            cycle_state["ready_symbols"].append(symbol)
            existing = await store.find_open_auto_trade(symbol, settings.default_interval)
            if existing:
                continue
            trade = {
                "id": str(uuid.uuid4()),
                "symbol": symbol,
                "direction": result["signal"],
                "timeframe": settings.default_interval,
                "signal_time": result.get("updated_at") or datetime.now(timezone.utc).isoformat(),
                "entry": result["entry"],
                "stop_loss": result["stop_loss"],
                "take_profit_1": result["take_profit_1"],
                "take_profit_2": result["take_profit_2"],
                "risk_reward": result["rr"],
                "confidence": result["confidence"],
                "regime": result["regime"],
                "structure_state": result.get("structure"),
                "liquidity_state": result.get("liquidity"),
                "volume_state": result.get("volume"),
                "momentum_state": result.get("momentum"),
                "status": "OPEN",
                "source": "auto_signal",
                "auto_created": True,
                "asset_profile": result.get("asset_profile"),
                "signal_reasons": result.get("reasons", []),
                "mtf_alignment": result.get("mtf_alignment"),
                "mtf_vetoes": result.get("mtf_vetoes", []),
                "mtf_timeframes": result.get("timeframes", {}),
            }
            saved_trade = await store.create_trade(trade)
            saved.append(saved_trade)
            asyncio.create_task(_notify_trade_opened(saved_trade))
            asyncio.create_task(_notify_telegram_trade_opened(saved_trade))
        except Exception as exc:
            log.warning("auto signal scan failed for %s: %s", symbol, exc)
    return saved


async def _notify_trade_opened(trade: dict) -> None:
    try:
        result = await push_notifier.trade_opened(trade)
        if result["sent"] or result["failed"] or result["removed"]:
            push_log.info("trade-open notification: %s", result)
    except Exception:
        push_log.exception("trade-open notification failed")


async def _notify_trade_closed(trade: dict) -> None:
    try:
        result = await push_notifier.trade_closed(trade)
        if result["sent"] or result["failed"] or result["removed"]:
            push_log.info("trade-close notification: %s", result)
    except Exception:
        push_log.exception("trade-close notification failed")


async def _notify_telegram_trade_opened(trade: dict) -> None:
    result = await telegram_notifier.trade_opened(trade)
    if result["sent"] or result["failed"]:
        log.info("telegram trade-open notification: %s", result)


async def _notify_telegram_trade_closed(trade: dict) -> None:
    result = await telegram_notifier.trade_closed(trade)
    if result["sent"] or result["failed"]:
        log.info("telegram trade-close notification: %s", result)


async def _auto_signal_loop():
    while True:
        cycle_started = datetime.now(timezone.utc)
        cycle_state.update({
            "status": "CHECKING",
            "started_at": cycle_started.isoformat(),
            "run_id": cycle_state.get("run_id", 0) + 1,
            "finished_at": None,
            "scanned_symbols": 0,
            "ready_signals": 0,
            "ready_symbols": [],
            "saved_trades": 0,
            "last_saved_symbols": [],
            "last_error": None,
        })
        try:
            if not store.has_persistent_storage:
                await store.check_persistent_storage()
            if store.has_persistent_storage:
                cycle_state["scanned_symbols"] = len(settings.symbol_list)
                saved = await _scan_and_store_auto_signals()
                cycle_state["saved_trades"] = len(saved)
                cycle_state["last_saved_symbols"] = [trade.get("symbol") for trade in saved]
                cycle_state["completed_cycles"] += 1
                if saved:
                    log.info("saved %d automatic paper signal(s)", len(saved))
                delay = AUTO_SCAN_SECONDS
                cycle_state["status"] = "IDLE"
            else:
                log.warning("automatic signal scan paused: backend=%s error=%s", store.backend_name, store.storage_last_error)
                delay = STORAGE_RETRY_SECONDS
                cycle_state["status"] = "WAITING_STORAGE"
                cycle_state["last_error"] = store.storage_last_error
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("automatic signal loop failed: %s", exc)
            delay = STORAGE_RETRY_SECONDS
            cycle_state["status"] = "ERROR"
            cycle_state["last_error"] = type(exc).__name__
        cycle_finished = datetime.now(timezone.utc)
        cycle_state["finished_at"] = cycle_finished.isoformat()
        cycle_state["last_run_duration_seconds"] = round((cycle_finished - cycle_started).total_seconds(), 3)
        cycle_state["next_run_at"] = (cycle_finished.timestamp() + delay)
        await asyncio.sleep(delay)


def evaluate_trade_exit(trade: dict, current_price: float) -> dict | None:
    try:
        current = float(current_price)
        entry = float(trade["entry"])
        stop_loss = float(trade["stop_loss"])
        take_profit_1 = float(trade["take_profit_1"])
    except (KeyError, TypeError, ValueError):
        return None

    direction = trade.get("direction")
    if direction == "LONG":
        stopped = current <= stop_loss
        target_hit = current >= take_profit_1
        gross_pnl = (current - entry) / max(abs(entry), 1e-9) * 100
    elif direction == "SHORT":
        stopped = current >= stop_loss
        target_hit = current <= take_profit_1
        gross_pnl = (entry - current) / max(abs(entry), 1e-9) * 100
    else:
        return None

    if not stopped and not target_hit:
        return None
    reason = "STOP_LOSS" if stopped else "TAKE_PROFIT_1"
    return {
        "status": "STOPPED" if stopped else "CLOSED",
        "result": "LOSS" if stopped else "WIN",
        "pnl": round(gross_pnl, 8),
        "exit_reason": reason,
        "exit_price": round(current, 8),
        "closed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _manage_open_trades():
    while True:
        try:
            for trade in await store.list_active_trades():
                symbol = str(trade.get("symbol", "")).upper()
                price = market.tickers.get(symbol, {}).get("price")
                if not symbol or price is None:
                    continue
                patch = evaluate_trade_exit(trade, price)
                if not patch:
                    continue
                updated = await store.update_trade(str(trade["id"]), patch)
                if updated:
                    log.info("trade %s closed at %s: %s price=%s", trade.get("id"), symbol, patch["exit_reason"], price)
                    asyncio.create_task(_notify_trade_closed(updated))
                    asyncio.create_task(_notify_telegram_trade_closed(updated))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("trade exit manager failed: %s", exc)
        await asyncio.sleep(EXIT_SCAN_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await store.check_persistent_storage()
    await market.start()
    auto_task = asyncio.create_task(_auto_signal_loop())
    telegram_task = asyncio.create_task(telegram_bot.run()) if telegram_bot.configured else None
    if not store.has_persistent_storage:
        log.error("automatic signal loop waiting for persistent Supabase storage; backend=%s error=%s", store.backend_name, store.storage_last_error)
    exit_task = asyncio.create_task(_manage_open_trades())
    try:
        yield
    finally:
        if auto_task:
            auto_task.cancel()
        if telegram_task:
            telegram_task.cancel()
        exit_task.cancel()
        tasks = [exit_task] + ([auto_task] if auto_task else []) + ([telegram_task] if telegram_task else [])
        await asyncio.gather(*tasks, return_exceptions=True)
        await market.stop()

app = FastAPI(title="Weeg Crypto Trading Intelligence", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_list if settings.cors_origins != "*" else ["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def index(): return FileResponse(Path("templates/index.html"))


@app.get("/push-sw.js")
async def push_service_worker():
    return FileResponse(
        Path("static/push-sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/api/push/config")
async def push_config():
    return {"enabled": push_notifier.configured, "public_key": push_notifier.public_key}


@app.post("/api/push/subscribe")
async def push_subscribe(subscription: PushSubscriptionInput):
    if not push_notifier.configured:
        raise HTTPException(status_code=503, detail="إشعارات الهاتف غير مهيأة في الخادم")
    saved = await store.upsert_push_subscription(subscription.model_dump())
    return {"ok": True, "endpoint": saved.get("endpoint"), "subscriptions": len(await store.list_push_subscriptions())}


@app.delete("/api/push/subscribe")
async def push_unsubscribe(endpoint: str):
    return {"ok": await store.delete_push_subscription(endpoint)}


@app.get("/api/health")
async def health(response: Response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    persistent = store.has_persistent_storage
    return {
        "status": "ok",
        "service": "weeg",
        "symbols": len(settings.symbol_list),
        **market.health_snapshot(),
        "storage_backend": store.backend_name,
        "postgres_configured": store.postgres_configured,
        "database_url_configured": bool(settings.postgres_dsn),
        "supabase_url_configured": bool(settings.supabase_http_url),
        "supabase_key_configured": bool(settings.supabase_auth_keys),
        "supabase_key_count": store.supabase_key_count,
        "supabase_key_source": store.storage_key_source,
        "persistent_storage_configured": store.persistent_storage_configured,
        "persistent_storage": persistent,
        "storage_last_error": store.storage_last_error,
        "storage_last_check_at": store.storage_last_check_at,
        "telegram_notifications_enabled": telegram_notifier.configured,
        "telegram_controls_enabled": telegram_bot.configured,
        "auto_signal_enabled": persistent,
        "auto_signal_storage": persistent,
        "warning": None if persistent else "التخزين الدائم غير جاهز؛ الفحص الآلي ينتظر اتصال Supabase ولن يحفظ صفقات في SQLite المؤقت",
    }

@app.get("/api/summary/cycle/state")
async def summary_cycle_state(response: Response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cycle": {**cycle_state, "ready_symbols": list(cycle_state.get("ready_symbols", []))},
        "health": {
            "storage_backend": store.backend_name,
            "persistent_storage": store.has_persistent_storage,
            "auto_signal_enabled": store.has_persistent_storage,
            **market.health_snapshot(),
            "storage_last_error": store.storage_last_error,
        },
    }

@app.get("/api/summary/cycle")
async def summary_cycle(response: Response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    warnings = []
    open_trades = []
    closed_trades = []
    if store.has_persistent_storage:
        results = await asyncio.gather(
            store.list_active_trades(),
            store.list_trades("CLOSED_OR_STOPPED"),
            return_exceptions=True,
        )
        if isinstance(results[0], Exception):
            warnings.append(f"open_trades:{type(results[0]).__name__}")
        else:
            open_trades = results[0]
        if isinstance(results[1], Exception):
            warnings.append(f"closed_trades:{type(results[1]).__name__}")
        else:
            closed_trades = results[1]
    else:
        warnings.append("persistent_storage_unavailable")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cycle": cycle_state,
        "warnings": warnings,
        "health": {
            "storage_backend": store.backend_name,
            "persistent_storage": store.has_persistent_storage,
            "auto_signal_enabled": store.has_persistent_storage,
            **market.health_snapshot(),
            "storage_last_error": store.storage_last_error,
        },
        "trades": {
            "open": len(open_trades),
            "closed": len(closed_trades),
            "latest_open": [trade.get("symbol") for trade in open_trades[:5]],
        },
        "configuration": {
            "symbols": len(settings.symbol_list),
            "interval": settings.default_interval,
            "decision_timeframes": list(MTF_INTERVALS),
            "scan_seconds": AUTO_SCAN_SECONDS,
            "confidence_threshold": settings.confidence_threshold,
            "minimum_rr": settings.minimum_rr,
        },
    }

@app.get("/api/market/candles")
async def candles(symbol: str = "BTCUSDT", interval: str = "15m", limit: int = 250):
    symbol = symbol.upper()
    if symbol not in settings.symbol_list: raise HTTPException(404, "العملة غير موجودة في القائمة")
    rows = await market.ensure_history(symbol, interval)
    return {"symbol": symbol, "interval": interval, "candles": rows[-min(limit, 500):]}

@app.get("/api/market/overview")
async def overview(interval: str = "15m"):
    async def one(symbol: str):
        try:
            if interval == "15m":
                result = await _analyze_mtf(symbol)
            else:
                rows = await market.ensure_history(symbol, interval)
                result = analyze(symbol, rows, interval, settings.confidence_threshold, settings.minimum_rr)
            result["ticker"] = market.tickers.get(symbol, {})
            return result
        except Exception as exc:
            return {"symbol": symbol, "signal": "NO TRADE", "confidence": 0, "reason": str(exc), "ready": False}
    return sorted(await asyncio.gather(*(one(s) for s in settings.symbol_list)), key=lambda x: (x.get("confidence", 0), x.get("rr", 0)), reverse=True)

@app.get("/api/signals/{symbol}")
async def signal(symbol: str, interval: str = "15m"):
    symbol = symbol.upper()
    if interval == "15m":
        return await _analyze_mtf(symbol)
    rows = await market.ensure_history(symbol, interval)
    return analyze(symbol, rows, interval, settings.confidence_threshold, settings.minimum_rr)

@app.get("/api/backtest/{symbol}")
async def backtest(symbol: str, interval: str = "15m", limit: int = 500):
    symbol = symbol.upper(); rows = await market.ensure_history(symbol, interval)
    return run_backtest(symbol, rows[-min(limit, 500):], interval, threshold=settings.confidence_threshold, minimum_rr=settings.minimum_rr)

@app.get("/api/trades")
async def trades(status: str | None = None): return await store.list_trades(status.upper() if status else None)

@app.post("/api/trades/paper")
async def create_paper_trade(payload: TradeInput):
    if payload.direction not in ("LONG", "SHORT"): raise HTTPException(400, "direction must be LONG or SHORT")
    trade = {"id": str(uuid.uuid4()), **payload.model_dump(), "symbol": payload.symbol.upper(), "status": "OPEN", "signal_time": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}
    return await store.create_trade(trade)

@app.patch("/api/trades/{trade_id}")
async def update_trade(trade_id: str, patch: dict):
    result = await store.update_trade(trade_id, patch)
    if result is None: raise HTTPException(404, "الصفقة غير موجودة")
    return result

@app.get("/api/settings")
async def get_app_settings():
    saved = await store.get_settings()
    return {"symbols": settings.symbol_list, "confidence_threshold": settings.confidence_threshold, "minimum_rr": settings.minimum_rr, "risk_per_trade": settings.risk_per_trade, **saved}

@app.post("/api/settings")
async def save_app_settings(payload: SettingsInput):
    data = payload.model_dump(exclude_none=True)
    if "symbols" in data: data["symbols"] = [s.upper() for s in data["symbols"]]
    return await store.save_settings(data)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept(); queue = market.subscribe()
    try:
        await websocket.send_json({"type": "connected", "symbols": settings.symbol_list})
        while True: await websocket.send_json(await queue.get())
    except (WebSocketDisconnect, asyncio.CancelledError): pass
    finally: market.unsubscribe(queue)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(__import__("os").getenv("PORT", "10000")))
