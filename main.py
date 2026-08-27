from __future__ import annotations
import asyncio, logging, uuid
from contextlib import asynccontextmanager
from typing import Any
from datetime import datetime, timezone
from pathlib import Path
from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from app.config import get_settings
from app.data.market import MarketData
from app.analysis.engine import analyze
from app.analysis.safety import assess_entry, candle_age_seconds, closed_candles, iso_from_epoch
from app.analysis.backtest import run_backtest
from app.storage.store import Store
from app.notifications import PushNotifier
from app.telegram import TelegramBotController, TelegramNotifier
from app.auth import require_user
from app.strategies.ifvg.service import IFVGService
from app.strategies.ifvg.backtest import run_ifvg_backtest

settings = get_settings()
market = MarketData(settings.binance_rest_url, settings.binance_ws_url, settings.symbol_list, settings.default_interval, ["5m", "15m", "1h", "4h"])
store = Store(settings.database_path, settings.supabase_http_url, settings.supabase_auth_keys, settings.redis_url, settings.postgres_dsn)
push_notifier = PushNotifier(store, settings.vapid_private_key, settings.vapid_subject)
telegram_notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
log = logging.getLogger("weeg.auto_signals")
push_log = logging.getLogger("weeg.push")
AUTO_SCAN_SECONDS = 60
STORAGE_RETRY_SECONDS = 30
EXIT_SCAN_SECONDS = 5
SHADOW_OUTCOME_HORIZON_SECONDS = 24 * 60 * 60
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
    "shadow_signals": 0,
    "shadow_blocked": 0,
    "shadow_warnings": 0,
    "shadow_outcome_updates": 0,
}
telegram_bot = TelegramBotController(telegram_notifier, store, market, settings, cycle_state)

async def _ifvg_event_callback(event: dict[str, Any]) -> None:
    event_type = event.get("type")
    trade = event.get("trade") or {}
    log.info("IFVG paper event=%s trade=%s symbol=%s", event_type, trade.get("id"), trade.get("symbol"))
    if event_type == "ifvg_trade_opened":
        asyncio.create_task(push_notifier.ifvg_trade_opened(trade))
        asyncio.create_task(telegram_notifier.ifvg_trade_opened(trade))
    elif event_type == "ifvg_trade_closed":
        asyncio.create_task(push_notifier.ifvg_trade_closed(trade))
        asyncio.create_task(telegram_notifier.ifvg_trade_closed(trade))

ifvg_service = IFVGService(settings, market, store, event_callback=_ifvg_event_callback)
telegram_bot.ifvg_service = ifvg_service

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


def _latest_closed_time(rows: list[dict], interval: str) -> float | None:
    rows = closed_candles(rows, interval)
    return float(rows[-1]["time"]) if rows else None


def _mtf_data_veto(rows_by_interval: dict[str, list[dict]]) -> list[str]:
    latest = {interval: _latest_closed_time(rows, interval) for interval, rows in rows_by_interval.items()}
    if any(value is None for value in latest.values()):
        return [f"لا توجد شمعة مغلقة فعليًا في {interval}" for interval, value in latest.items() if value is None]
    entry_time = latest["15m"]
    vetoes = []
    for interval in ("1h", "4h"):
        if latest[interval] > entry_time + settings.mtf_sync_tolerance_seconds:
            vetoes.append(f"بيانات {interval} أحدث من شمعة 15m المستخدمة")
    return vetoes


async def _analyze_mtf(symbol: str) -> dict:
    rows_4h, rows_1h, rows_15m = await asyncio.gather(
        market.ensure_history(symbol, "4h"),
        market.ensure_history(symbol, "1h"),
        market.ensure_history(symbol, "15m"),
    )
    rows_by_interval = {"4h": rows_4h, "1h": rows_1h, "15m": rows_15m}
    quality_by_interval = {interval: market.data_quality_snapshot(symbol, interval) for interval in MTF_INTERVALS}
    data_vetoes = _mtf_data_veto(rows_by_interval) if any(rows_by_interval.values()) else []
    if any(rows_by_interval.values()):
        data_vetoes.extend(market.data_quality_vetoes(symbol, MTF_INTERVALS))
    results = {
        "4h": analyze(symbol, rows_4h, "4h", settings.confidence_threshold, settings.minimum_rr),
        "1h": analyze(symbol, rows_1h, "1h", settings.confidence_threshold, settings.minimum_rr),
        "15m": analyze(symbol, rows_15m, "15m", settings.confidence_threshold, settings.minimum_rr),
    }
    entry = results["15m"]
    entry_signal = entry.get("signal")
    candidate_direction = entry.get("bias") if entry_signal not in ("LONG", "SHORT") else entry_signal
    base_components = dict(entry.get("reversal_risk_components") or {})
    if candidate_direction in ("LONG", "SHORT"):
        higher_timeframe_risk = int(
            results["1h"].get("regime") == "TRANSITION"
            or results["4h"].get("regime") == "TRANSITION"
            or results["1h"].get("signal") != candidate_direction
            or results["4h"].get("signal") != candidate_direction
        )
        base_components["higher_timeframe"] = higher_timeframe_risk
        entry["reversal_risk_components"] = base_components
        entry["reversal_risk"] = sum(base_components.values())
    timeframe_signals = {interval: results[interval].get("signal") for interval in MTF_INTERVALS}
    timeframe_ready = {interval: bool(results[interval].get("ready")) for interval in MTF_INTERVALS}
    vetoes = [*data_vetoes]
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
        not data_vetoes
        and entry_signal in ("LONG", "SHORT")
        and all(timeframe_signals[interval] == entry_signal for interval in MTF_INTERVALS)
        and all(timeframe_ready.values())
    )
    entry = {
        **entry,
        "timeframes": {
            interval: {key: result.get(key) for key in ("signal", "bias", "confidence", "structure", "regime", "ready", "signal_candle_time", "signal_age_seconds")}
            for interval, result in results.items()
        },
        "mtf_alignment": "ALIGNED" if fully_aligned else "VETO",
        "mtf_vetoes": vetoes,
        "data_quality": quality_by_interval,
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


async def _record_shadow_signal(symbol: str, result: dict, live_entry: float | None, safety: Any | None, extra_blocks: list[str] | None = None) -> dict:
    extra_blocks = list(extra_blocks or [])
    signal_candle_time = result.get("signal_candle_time") or iso_from_epoch(result.get("signal_time"))
    if not signal_candle_time:
        signal_candle_time = datetime.now(timezone.utc).isoformat()
    blocked_reasons = [*(safety.blocked_reasons if safety else []), *extra_blocks]
    warning_reasons = list(safety.warning_reasons if safety else [])
    if extra_blocks and "STALE_SIGNAL" in extra_blocks:
        warning_reasons.append("SIGNAL_AGE_ABOVE_MAXIMUM")
    row = {
        "id": str(uuid.uuid4()),
        "symbol": symbol,
        "timeframe": settings.default_interval,
        "direction": result.get("signal"),
        "decision_time": datetime.now(timezone.utc).isoformat(),
        "signal_candle_time": signal_candle_time,
        "signal_age_seconds": result.get("signal_age_seconds"),
        "market_data_asof": result.get("market_data_asof"),
        "signal_price": result.get("signal_price") or result.get("price") or result.get("entry"),
        "simulated_entry_price": safety.entry_price if safety else live_entry,
        "simulated_stop_loss": safety.stop_loss if safety else result.get("stop_loss"),
        "simulated_take_profit_1": safety.take_profit_1 if safety else result.get("take_profit_1"),
        "simulated_take_profit_2": safety.take_profit_2 if safety else result.get("take_profit_2"),
        "entry_deviation_pct": safety.entry_deviation_pct if safety else None,
        "monitor_entry_limit_pct": safety.monitor_limit if safety else None,
        "expected_rr_after_execution": safety.expected_rr_after_execution if safety else None,
        "regime": result.get("regime"),
        "mtf_alignment": result.get("mtf_alignment"),
        "reversal_risk": result.get("reversal_risk", 0),
        "reversal_risk_components": result.get("reversal_risk_components", {}),
        "overextension_metrics": result.get("overextension_metrics", {}),
        "would_have_executed": not blocked_reasons,
        "would_block": bool(blocked_reasons),
        "blocked_reasons": blocked_reasons,
        "warning_reasons": warning_reasons,
        "outcome_status": "PENDING",
    }
    saved = await store.create_shadow_signal(row)
    cycle_state["shadow_signals"] += 1
    if saved.get("would_block"):
        cycle_state["shadow_blocked"] += 1
    if saved.get("warning_reasons"):
        cycle_state["shadow_warnings"] += 1
    return saved


async def _evaluate_shadow_outcomes() -> int:
    updated_count = 0
    try:
        pending = [row for row in await store.list_shadow_signals(500) if (row.get("outcome_status") or "PENDING") == "PENDING"]
    except Exception as exc:
        log.warning("shadow outcome scan failed: %s", exc)
        return 0
    for row in pending:
        decision_time = row.get("decision_time")
        if decision_time:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(decision_time).replace("Z", "+00:00"))).total_seconds()
                if age > SHADOW_OUTCOME_HORIZON_SECONDS:
                    await store.update_shadow_signal(str(row["id"]), {"outcome_status": "EXPIRED", "outcome_checked_at": datetime.now(timezone.utc).isoformat()})
                    updated_count += 1
                    continue
            except (TypeError, ValueError):
                log.warning("invalid shadow decision_time for %s", row.get("id"))
        symbol = str(row.get("symbol") or "").upper()
        price = (market.tickers.get(symbol) or {}).get("price")
        if not symbol or price is None or not row.get("direction"):
            continue
        simulated = {
            "direction": row.get("direction"),
            "entry": row.get("simulated_entry_price"),
            "stop_loss": row.get("simulated_stop_loss"),
            "take_profit_1": row.get("simulated_take_profit_1"),
        }
        patch = evaluate_trade_exit(simulated, float(price))
        if not patch:
            continue
        outcome = await store.update_shadow_signal(str(row["id"]), {
            "outcome_status": "LOSS" if patch["result"] == "LOSS" else "WIN",
            "outcome_pnl": patch["pnl"],
            "outcome_checked_at": datetime.now(timezone.utc).isoformat(),
        })
        if outcome:
            updated_count += 1
    cycle_state["shadow_outcome_updates"] += updated_count
    return updated_count


async def _scan_and_store_auto_signals() -> list[dict]:
    saved = []
    ready_signals = 0
    for symbol in settings.symbol_list:
        try:
            result = await _analyze_mtf(symbol)
            actual_ready = bool(result.get("ready") and result.get("signal") in ("LONG", "SHORT"))
            candidate_direction = result.get("signal") if actual_ready else result.get("bias")
            live_entry = (market.tickers.get(symbol) or {}).get("price")
            ticker_quality = market.ticker_quality_snapshot(symbol)
            extra_blocks = [] if actual_ready else ["BASE_SIGNAL_NOT_READY"]
            if not ticker_quality["fresh"]:
                extra_blocks.append("STALE_OR_MISSING_LIVE_TICKER")
            age = result.get("signal_age_seconds")
            if age is not None and float(age) > settings.signal_max_age_seconds:
                extra_blocks.append("STALE_SIGNAL")
            if live_entry is None:
                extra_blocks.append("MISSING_LIVE_TICKER")
            safety = None
            candidate = {**result, "signal": candidate_direction} if candidate_direction in ("LONG", "SHORT") else result
            if ticker_quality["fresh"] and live_entry is not None and candidate_direction in ("LONG", "SHORT"):
                safety = assess_entry(candidate, float(live_entry), result.get("applied_minimum_rr", settings.minimum_rr))
            if settings.weeg_shadow_mode and candidate_direction in ("LONG", "SHORT"):
                await _record_shadow_signal(symbol, candidate, live_entry, safety, extra_blocks)
            if not actual_ready:
                continue
            ready_signals += 1
            cycle_state["ready_signals"] = ready_signals
            cycle_state["ready_symbols"].append(symbol)
            if extra_blocks or (safety and safety.would_block and settings.weeg_safety_gates_enabled):
                continue
            existing = await store.find_open_auto_trade(symbol, settings.default_interval)
            if existing:
                continue
            same_signal = await store.find_auto_trade_signal(symbol, settings.default_interval, result.get("signal_candle_time"))
            if same_signal:
                continue
            if safety is None:
                continue
            trade = {
                "id": str(uuid.uuid4()),
                "symbol": symbol,
                "direction": result["signal"],
                "timeframe": settings.default_interval,
                "signal_time": result.get("updated_at") or datetime.now(timezone.utc).isoformat(),
                "signal_candle_time": result.get("signal_candle_time"),
                "signal_age_seconds": result.get("signal_age_seconds"),
                "market_data_asof": result.get("market_data_asof"),
                "signal_price": safety.signal_price,
                "entry": safety.entry_price,
                "entry_deviation_pct": safety.entry_deviation_pct,
                "monitor_entry_limit_pct": safety.monitor_limit,
                "expected_rr_after_execution": safety.expected_rr_after_execution,
                "stop_loss": safety.stop_loss,
                "take_profit_1": safety.take_profit_1,
                "take_profit_2": safety.take_profit_2,
                "risk_reward": safety.expected_rr_after_execution,
                "confidence": result["confidence"],
                "regime": result["regime"],
                "structure_state": result.get("structure"),
                "liquidity_state": result.get("liquidity"),
                "volume_state": result.get("volume"),
                "momentum_state": result.get("momentum"),
                "reversal_risk": safety.reversal_risk,
                "reversal_risk_components": safety.reversal_risk_components,
                "overextension_metrics": safety.overextension_metrics,
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
            rest = market.rest_health() if hasattr(market, "rest_health") else {"available": True}
            if not rest.get("available", True):
                log.warning("automatic signal scan paused: Binance REST unavailable for %ss after %s", rest.get("retry_after_seconds", 0), exc)
                break
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


trade_tracking: dict[str, dict[str, Any]] = {}


def _excursion_patch(trade: dict, current_price: float, force: bool = False) -> dict[str, Any]:
    trade_id = str(trade.get("id"))
    try:
        entry = float(trade["entry"])
        stop_loss = float(trade["stop_loss"])
        current = float(current_price)
    except (KeyError, TypeError, ValueError):
        return {}
    if entry == 0:
        return {}
    direction = str(trade.get("direction") or "").upper()
    move_pct = ((current - entry) / abs(entry) * 100) if direction == "LONG" else ((entry - current) / abs(entry) * 100) if direction == "SHORT" else 0.0
    risk = abs(entry - stop_loss)
    favorable_distance = (current - entry) if direction == "LONG" else (entry - current) if direction == "SHORT" else 0.0
    state = trade_tracking.setdefault(trade_id, {"mfe": float(trade.get("max_favorable_excursion") or 0.0), "mae": float(trade.get("max_adverse_excursion") or 0.0), "last_persist": 0.0, "plus_one_r_logged": False})
    new_record = move_pct > state["mfe"] or move_pct < state["mae"]
    state["mfe"] = max(state["mfe"], move_pct)
    state["mae"] = min(state["mae"], move_pct)
    now = datetime.now(timezone.utc).timestamp()
    plus_one_r = risk > 0 and favorable_distance >= risk
    if plus_one_r and not state["plus_one_r_logged"]:
        state["plus_one_r_logged"] = True
        log.info("shadow +1R candidate reached for trade %s; no production stop move applied", trade_id)
    if not force and now - state["last_persist"] < 45:
        return {}
    state["last_persist"] = now
    return {
        "max_favorable_excursion": round(state["mfe"], 8),
        "max_adverse_excursion": round(state["mae"], 8),
        "exit_checked_at": datetime.now(timezone.utc).isoformat(),
    }


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
        "exit_checked_at": datetime.now(timezone.utc).isoformat(),
        "stop_moved_to_breakeven": False,
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
                exit_patch = evaluate_trade_exit(trade, price)
                excursion = _excursion_patch(trade, price, force=bool(exit_patch)) if settings.weeg_mfe_shadow else {}
                patch = {**excursion, **exit_patch} if exit_patch else excursion
                if not patch:
                    continue
                updated = await store.update_trade(str(trade["id"]), patch)
                if updated and exit_patch:
                    log.info("trade %s closed at %s: %s price=%s", trade.get("id"), symbol, exit_patch["exit_reason"], price)
                    trade_tracking.pop(str(trade["id"]), None)
                    asyncio.create_task(_notify_trade_closed(updated))
                    asyncio.create_task(_notify_telegram_trade_closed(updated))
                elif updated:
                    log.debug("trade %s excursion telemetry updated at price=%s", trade.get("id"), price)
            await _evaluate_shadow_outcomes()
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
    await ifvg_service.start()
    try:
        yield
    finally:
        if auto_task:
            auto_task.cancel()
        if telegram_task:
            telegram_task.cancel()
        exit_task.cancel()
        await ifvg_service.stop()
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


@app.get("/api/auth/config")
async def auth_config():
    return {"enabled": bool(settings.supabase_public_key), "url": settings.supabase_http_url if settings.supabase_public_key else None, "public_key": settings.supabase_public_key}

@app.get("/api/push/config")
async def push_config():
    return {"enabled": push_notifier.configured, "public_key": push_notifier.public_key}


@app.post("/api/push/subscribe")
async def push_subscribe(subscription: PushSubscriptionInput, user: dict = Depends(require_user)):
    if not push_notifier.configured:
        raise HTTPException(status_code=503, detail="إشعارات الهاتف غير مهيأة في الخادم")
    saved = await store.upsert_push_subscription(subscription.model_dump(), user_id=str(user["id"]))
    return {"ok": True, "endpoint": saved.get("endpoint"), "subscriptions": len(await store.list_push_subscriptions(user_id=str(user["id"]))) }


@app.delete("/api/push/subscribe")
async def push_unsubscribe(endpoint: str, user: dict = Depends(require_user)):
    return {"ok": await store.delete_push_subscription(endpoint, user_id=str(user["id"]))}


@app.get("/api/healthz")
async def healthz():
    return {"status": "ok", "service": "weeg"}

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
        "shadow_mode": settings.weeg_shadow_mode,
        "safety_gates_enabled": settings.weeg_safety_gates_enabled,
        "mfe_shadow": settings.weeg_mfe_shadow,
        "ifvg": ifvg_service.health(),
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

@app.get("/api/summary/shadow")
async def summary_shadow(limit: int = 200):
    rows = await store.list_shadow_signals(limit=min(max(limit, 1), 500))
    blocked = [row for row in rows if row.get("would_block")]
    warnings = [row for row in rows if row.get("warning_reasons")]
    outcomes = {}
    for row in rows:
        outcome = row.get("outcome_status") or "PENDING"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sample_size": len(rows),
        "blocked_count": len(blocked),
        "warning_count": len(warnings),
        "blocked_rate_pct": round(len(blocked) / len(rows) * 100, 3) if rows else 0,
        "outcomes": outcomes,
        "recent": rows[:min(limit, 50)],
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
    return {"symbol": symbol, "interval": interval, "candles": rows[-min(limit, 500):], "data_quality": market.data_quality_snapshot(symbol, interval)}

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
            result.setdefault("data_quality", market.data_quality_snapshot(symbol, interval))
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
    result = analyze(symbol, rows, interval, settings.confidence_threshold, settings.minimum_rr)
    result["data_quality"] = market.data_quality_snapshot(symbol, interval)
    return result

@app.get("/api/backtest/{symbol}")
async def backtest(symbol: str, interval: str = "15m", limit: int = 500, user: dict = Depends(require_user)):
    symbol = symbol.upper()
    capped_limit = min(max(limit, 80), 500)
    rows = await market.ensure_history(symbol, interval)
    mtf_rows = None
    if interval == "15m":
        rows_4h, rows_1h = await asyncio.gather(market.ensure_history(symbol, "4h"), market.ensure_history(symbol, "1h"))
        mtf_rows = {"1h": rows_1h, "4h": rows_4h}
    return run_backtest(symbol, rows[-capped_limit:], interval, threshold=settings.confidence_threshold, minimum_rr=settings.minimum_rr, mtf_candles=mtf_rows)

@app.get("/api/ifvg/health")
async def ifvg_health():
    return ifvg_service.health()

@app.get("/api/ifvg/decision/{symbol}")
async def ifvg_decision(symbol: str):
    symbol = symbol.upper()
    if symbol not in settings.ifvg_symbol_list:
        raise HTTPException(404, "العملة غير موجودة في قائمة IFVG")
    return await ifvg_service.scan_symbol(symbol, persist=False)

@app.get("/api/ifvg/setups")
async def ifvg_setups(state: str | None = None, symbol: str | None = None, limit: int = 200):
    return await store.list_ifvg_setups(state=state, symbol=symbol, limit=limit)

@app.get("/api/ifvg/trades")
async def ifvg_trades(state: str | None = None, limit: int = 200):
    return await store.list_ifvg_trades(state=state, limit=limit)

@app.get("/api/ifvg/trades/{trade_id}/fills")
async def ifvg_trade_fills(trade_id: str):
    trades = await store.list_ifvg_trades(limit=500)
    if not any(str(trade.get("id")) == trade_id for trade in trades):
        raise HTTPException(404, "صفقة IFVG غير موجودة")
    return await store.list_ifvg_fills(trade_id)

@app.get("/api/ifvg/backtest/{symbol}")
async def ifvg_backtest(symbol: str, days: int = 180):
    symbol = symbol.upper()
    if symbol not in settings.ifvg_symbol_list:
        raise HTTPException(404, "العملة غير موجودة في قائمة IFVG")
    days = min(max(int(days), 1), 180)
    intervals = ("4h", "1h", "15m", "5m")
    loaded = await asyncio.gather(*(market.load_history_window(symbol, interval, days) for interval in intervals))
    filters = await market.exchange_filters(symbol)
    return run_ifvg_backtest(symbol, dict(zip(intervals, loaded)), config=ifvg_service.config, market=filters, portfolio=ifvg_service._portfolio())

IFVG_ACTIVE_STATES = {"ENTRY_ELIGIBLE", "ORDER_INTENT", "ORDER_SUBMITTED", "ORDER_PARTIALLY_FILLED", "ORDER_FILLED", "POSITION_OPEN"}
IFVG_CLOSED_STATES = {"POSITION_CLOSED", "TP_FILLED", "STOP_TRIGGERED", "REJECTED", "AMBIGUOUS", "RECONCILIATION_REQUIRED"}

async def _ifvg_trade_rows(user_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    return await store.list_ifvg_trades(user_id=user_id, limit=min(max(limit, 1), 500))

@app.get("/api/ifvg/summary")
async def ifvg_summary():
    trades = await _ifvg_trade_rows()
    setups = await store.list_ifvg_setups(limit=500)
    state_counts: dict[str, int] = {}
    for trade in trades:
        state = str(trade.get("state") or "UNKNOWN")
        state_counts[state] = state_counts.get(state, 0) + 1
    return {"strategy_id": "IFVG_SPOT_V1_2", "health": ifvg_service.health(), "setups": len(setups), "trades": len(trades), "trade_states": state_counts, "paper_only": True}

@app.get("/api/ifvg/cycle/summary")
async def ifvg_cycle_summary():
    trades = await _ifvg_trade_rows()
    setups = await store.list_ifvg_setups(limit=500)
    health = ifvg_service.health()
    states: dict[str, int] = {}
    for trade in trades:
        state = str(trade.get("state") or "UNKNOWN")
        states[state] = states.get(state, 0) + 1
    return {"strategy_id": "IFVG_SPOT_V1_2", "cycle": {"status": health["status"], "completed_cycles": health["completed_cycles"], "scanned_symbols": health["last_run_count"], "ready_signals": health["last_entry_count"], "last_run_at": health["last_run_at"], "last_error": health["last_error"]}, "setups": {"total": len(setups), "states": {str(setup.get("state") or "UNKNOWN"): sum(1 for item in setups if str(item.get("state") or "UNKNOWN") == str(setup.get("state") or "UNKNOWN")) for setup in setups}}, "trades": {"open": sum(state in IFVG_ACTIVE_STATES for state in states for _ in range(states[state])), "closed": sum(state in IFVG_CLOSED_STATES for state in states for _ in range(states[state])), "states": states}, "paper_only": True}

@app.get("/api/ifvg/trades/open")
async def ifvg_open_trades():
    rows = await _ifvg_trade_rows()
    return [trade for trade in rows if trade.get("state") in IFVG_ACTIVE_STATES]

@app.get("/api/ifvg/trades/closed")
async def ifvg_closed_trades():
    rows = await _ifvg_trade_rows()
    return [trade for trade in rows if trade.get("state") in IFVG_CLOSED_STATES or trade.get("closed_at")]

@app.get("/api/ifvg/performance")
async def ifvg_performance():
    rows = await _ifvg_trade_rows()
    closed = [trade for trade in rows if trade.get("closed_at") or trade.get("state") in IFVG_CLOSED_STATES]
    wins = sum(str(trade.get("result")) == "WIN" for trade in closed)
    losses = sum(str(trade.get("result")) == "LOSS" for trade in closed)
    pnl = sum(float(trade.get("realized_pnl_quote") or 0) for trade in closed)
    return {"strategy_id": "IFVG_SPOT_V1_2", "closed_trades": len(closed), "open_trades": sum(trade.get("state") in IFVG_ACTIVE_STATES for trade in rows), "wins": wins, "losses": losses, "win_rate_pct": round(wins / len(closed) * 100, 4) if closed else 0.0, "realized_pnl_quote": round(pnl, 8), "average_net_rr": round(sum(float(trade.get("net_rr") or 0) for trade in closed) / len(closed), 6) if closed else 0.0, "paper_only": True}

@app.get("/api/trades")
async def trades(status: str | None = None):
    return await store.list_trades(status.upper() if status else None)

@app.post("/api/trades/paper")
async def create_paper_trade(payload: TradeInput, user: dict = Depends(require_user)):
    if payload.direction not in ("LONG", "SHORT"): raise HTTPException(400, "direction must be LONG or SHORT")
    symbol = payload.symbol.upper()
    if symbol not in settings.symbol_list:
        raise HTTPException(400, "العملة غير موجودة في قائمة الأصول")
    levels_valid = ((payload.direction == "LONG" and payload.stop_loss < payload.entry < payload.take_profit_1 < payload.take_profit_2) or (payload.direction == "SHORT" and payload.take_profit_2 < payload.take_profit_1 < payload.entry < payload.stop_loss))
    if not levels_valid:
        raise HTTPException(400, "ترتيب مستويات الصفقة غير صالح")
    trade = {"id": str(uuid.uuid4()), **payload.model_dump(), "user_id": str(user["id"]), "symbol": symbol, "status": "OPEN", "source": "manual", "auto_created": False, "signal_time": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}
    return await store.create_trade(trade)

@app.patch("/api/trades/{trade_id}")
async def update_trade(trade_id: str, patch: dict, user: dict = Depends(require_user)):
    result = await store.update_trade(trade_id, patch, user_id=str(user["id"]))
    if result is None: raise HTTPException(404, "الصفقة غير موجودة")
    return result

@app.get("/api/settings")
async def get_app_settings(user: dict = Depends(require_user)):
    saved = await store.get_settings(user_id=str(user["id"]))
    return {"symbols": settings.symbol_list, "confidence_threshold": settings.confidence_threshold, "minimum_rr": settings.minimum_rr, "risk_per_trade": settings.risk_per_trade, **saved}

@app.post("/api/settings")
async def save_app_settings(payload: SettingsInput, user: dict = Depends(require_user)):
    data = payload.model_dump(exclude_none=True)
    if "symbols" in data: data["symbols"] = [s.upper() for s in data["symbols"]]
    return await store.save_settings(data, user_id=str(user["id"]))

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
