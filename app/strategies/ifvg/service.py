from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import time
import uuid
from typing import Any, Awaitable, Callable
from app.diagnostics import emit, exception_text

from app.strategies.ifvg.engine import IFVGConfig, STRATEGY_ID, analyze_ifvg
from app.strategies.ifvg.states import validate_transition

log = logging.getLogger("weeg.ifvg")


class IFVGService:
    """Isolated IFVG Spot paper-trading lifecycle.

    This service deliberately does not call any exchange order endpoint. Binance is used
    only for market data, exchange filters, and book-ticker observations.
    """

    def __init__(self, settings: Any, market: Any, store: Any, event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None):
        self.settings = settings
        self.market = market
        self.store = store
        self.event_callback = event_callback
        self.config = IFVGConfig(
            strategy_version="1.2.1",
            fee_bps=float(settings.ifvg_fee_bps),
            spread_bps=float(settings.ifvg_spread_bps),
            entry_slippage_bps=float(settings.ifvg_entry_slippage_bps),
            exit_slippage_bps=float(settings.ifvg_exit_slippage_bps),
            stop_slippage_bps=float(settings.ifvg_stop_slippage_bps),
            latency_bps=float(settings.ifvg_latency_bps),
        )
        self.task: asyncio.Task | None = None
        self.exit_task: asyncio.Task | None = None
        self.status = "DISABLED"
        self.last_error: str | None = None
        self.last_run_at: str | None = None
        self.last_run_count = 0
        self.last_entry_count = 0
        self.completed_cycles = 0

    @property
    def configured(self) -> bool:
        return bool(self.settings.ifvg_quote_balance is not None and self.settings.ifvg_max_position_value_quote is not None and self.settings.ifvg_max_global_open_positions is not None)

    def _portfolio(self) -> dict[str, Any]:
        return {
            "quote_balance": self.settings.ifvg_quote_balance,
            "eligible_equity": self.settings.ifvg_quote_balance,
            "max_position_value_quote": self.settings.ifvg_max_position_value_quote,
            "max_global_open_positions": self.settings.ifvg_max_global_open_positions,
            "daily_loss_fraction": self.settings.ifvg_daily_loss_fraction,
            "execution_liquidity_ok": True,
            "clock_skew_ms": 0,
        }

    async def _portfolio_snapshot(self) -> dict[str, Any]:
        portfolio = self._portfolio()
        if portfolio.get("quote_balance") is None:
            return portfolio
        try:
            trades = await self.store.list_ifvg_trades(user_id=self.settings.ifvg_user_id, limit=500)
        except Exception:
            return portfolio
        active_states = {"ENTRY_ELIGIBLE", "ORDER_INTENT", "ORDER_SUBMITTED", "ORDER_PARTIALLY_FILLED", "ORDER_FILLED", "POSITION_OPEN"}
        active = [trade for trade in trades if trade.get("state") in active_states]
        used_quote = sum(float(trade.get("entry_notional_quote") or (float(trade.get("entry_fill") or 0) * float(trade.get("quantity") or 0))) for trade in active)
        portfolio["quote_balance"] = max(0.0, float(portfolio["quote_balance"]) - used_quote)
        realized = sum(float(trade.get("realized_pnl_quote") or 0) for trade in trades if trade.get("closed_at"))
        portfolio["daily_loss_fraction"] = max(0.0, -realized / max(float(self.settings.ifvg_quote_balance), 1e-9))
        portfolio["open_positions_count"] = len(active)
        portfolio["active_entry_notional_quote"] = used_quote
        return portfolio

    async def _market_inputs(self, symbol: str, force_exchange_refresh: bool = False) -> dict[str, Any]:
        filters = await self.market.exchange_filters(symbol, force_refresh=force_exchange_refresh)
        inputs = dict(filters)
        try:
            book = await self.market.book_ticker(symbol, force_refresh=True)
            inputs.update({"entry_ask": book.get("ask"), "book_ticker": book})
        except Exception as exc:
            inputs["book_ticker_error"] = type(exc).__name__
            if self.settings.ifvg_orderbook_required:
                inputs["entry_ask"] = None
                inputs["target_bid"] = None
                inputs["stop_bid"] = None
        return inputs

    @staticmethod
    def _iso_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @classmethod
    def _iso_time(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        return str(value)

    async def _emit(self, payload: dict[str, Any]) -> None:
        if self.event_callback:
            try:
                await self.event_callback(payload)
            except Exception:
                log.exception("IFVG event callback failed")

    async def scan_symbol(self, symbol: str, persist: bool = True, force_exchange_refresh: bool = True, cycle_clock: dict[str, Any] | None = None) -> dict[str, Any]:
        symbol = symbol.upper()
        intervals = ("4h", "1h", "15m", "5m")
        loaded = await asyncio.gather(*(self.market.ensure_history(symbol, interval) for interval in intervals))
        rows = dict(zip(intervals, loaded))
        market_inputs = await self._market_inputs(symbol, force_exchange_refresh=force_exchange_refresh)
        market_inputs["live_decision"] = True
        market_inputs["decision_freshness"] = {interval: self.market.decision_data_quality_snapshot(symbol, interval) for interval in intervals}
        open_5m = self.market.current_open_candle(symbol, "5m")
        if open_5m:
            market_inputs["next_5m_open"] = open_5m.get("open")
            market_inputs["next_5m_open_time"] = open_5m.get("time")
        portfolio = await self._portfolio_snapshot()
        try:
            clock = cycle_clock if cycle_clock is not None else await self.market.clock_snapshot(force_refresh=True)
            portfolio["clock_skew_ms"] = clock.get("clock_offset_ms") if clock.get("clock_skew_ok") else None
            market_inputs["clock"] = clock
        except Exception as exc:
            portfolio["clock_skew_ms"] = None
            market_inputs["clock_error"] = type(exc).__name__
        result = analyze_ifvg(symbol, rows, self.config, market_inputs, portfolio)
        if not persist:
            result["config_version"] = self.settings.ifvg_config_version
            return result
        snapshot = {
            "id": str(uuid.uuid4()),
            "user_id": self.settings.ifvg_user_id,
            "symbol": symbol,
            "decision_time": self._iso_now(),
            "data_asof": self._iso_time(max((row.get("close_time", row.get("time", 0)) for series in rows.values() for row in series), default=None)),
            "config_version": self.settings.ifvg_config_version,
            "content_hash": result["data_snapshot_id"],
            "payload": {"rows": rows, "market": market_inputs, "portfolio": portfolio, "decision": result},
        }
        saved_snapshot = await self.store.create_ifvg_snapshot(snapshot)
        result["data_snapshot_id"] = saved_snapshot.get("id") or snapshot["content_hash"]
        result["config_version"] = self.settings.ifvg_config_version
        if result.get("setup_id"):
            setup_state = result.get("state_sequence", ["REJECTED"])[-1]
            setup = await self.store.create_ifvg_setup({
                "id": str(uuid.uuid4()),
                "user_id": self.settings.ifvg_user_id,
                "symbol": symbol,
                "source_fvg_id": result["setup_id"],
                "state": setup_state,
                "state_version": 1,
                "direction": "LONG",
                "zone_low": result.get("zone_low"),
                "zone_high": result.get("zone_high"),
                "sweep_time": self._iso_time(result.get("sweep_time")),
                "inversion_time": self._iso_time(result.get("inverted_at")),
                "retest_start_time": self._iso_time(result.get("retest_start_time")),
                "expires_at": None,
                "setup_snapshot_id": saved_snapshot.get("id"),
                "config_version": self.settings.ifvg_config_version,
                "score": result.get("score"),
                "score_version": self.settings.ifvg_config_version,
                "failed_gates": result.get("failed_gates", []),
                "metadata": result,
            })
            result["persistent_setup_id"] = setup.get("id")
            sequence = result.get("state_sequence") or []
            for index, state in enumerate(sequence):
                validate_transition(sequence[index - 1] if index else None, state)
                await self.store.create_ifvg_state_event({
                    "setup_id": setup.get("id"),
                    "from_state": sequence[index - 1] if index else None,
                    "to_state": state,
                    "reason_code": result.get("primary_rejection_reason") or "STATE_PROGRESS",
                    "reason_detail": None,
                    "event_time": snapshot["decision_time"],
                    "candle_time": self._iso_time(result.get("signal_candle_time")),
                    "data_snapshot_id": saved_snapshot.get("id"),
                    "metadata": {"strategy_id": STRATEGY_ID},
                })
            if result.get("decision") == "ENTRY_ELIGIBLE":
                await self._create_paper_entry(result, setup, market_inputs)
        return result

    async def _create_paper_entry(self, result: dict[str, Any], setup: dict[str, Any], market_inputs: dict[str, Any]) -> dict[str, Any] | None:
        symbol = result["symbol"]
        all_trades = await self.store.list_ifvg_trades(user_id=self.settings.ifvg_user_id, limit=500)
        if any(str(trade.get("setup_id")) == str(setup["id"]) for trade in all_trades):
            return None
        max_global = int(self.settings.ifvg_max_global_open_positions or 0)
        active_count = sum(1 for trade in all_trades if trade.get("state") in {"ENTRY_ELIGIBLE", "ORDER_INTENT", "ORDER_SUBMITTED", "ORDER_PARTIALLY_FILLED", "ORDER_FILLED", "POSITION_OPEN"})
        if max_global > 0 and active_count >= max_global:
            result["decision"] = "REJECTED"
            result.setdefault("failed_gates", []).append("POSITION_LIMIT_FAIL")
            result["primary_rejection_reason"] = "POSITION_LIMIT_FAIL"
            return None
        if await self.store.find_open_ifvg_trade(symbol, user_id=self.settings.ifvg_user_id):
            result["decision"] = "REJECTED"
            result.setdefault("failed_gates", []).append("POSITION_LIMIT_FAIL")
            result["primary_rejection_reason"] = "POSITION_LIMIT_FAIL"
            return None
        setup_id = setup["id"]
        reservation_key = f"{STRATEGY_ID}:{symbol}:{setup_id}:{result.get('signal_candle_time')}"
        reservation = await self.store.create_ifvg_reservation({
            "id": str(uuid.uuid4()),
            "user_id": self.settings.ifvg_user_id,
            "reservation_key": reservation_key,
            "symbol": symbol,
            "reserved_quantity": result.get("position_size") or 0,
            "reserved_quote": result.get("entry_notional_quote") or 0,
            "reserved_risk_quote": result.get("realized_risk_quote") or 0,
            "status": "ACTIVE",
            "metadata": {"data_snapshot_id": result.get("data_snapshot_id"), "strategy_id": STRATEGY_ID},
        })
        if not reservation:
            result["decision"] = "REJECTED"
            result.setdefault("failed_gates", []).append("POSITION_LIMIT_FAIL")
            result["primary_rejection_reason"] = "POSITION_LIMIT_FAIL"
            return None
        now = self._iso_now()
        try:
            trade = await self.store.create_ifvg_trade({
                "id": str(uuid.uuid4()),
                "user_id": self.settings.ifvg_user_id,
                "setup_id": setup_id,
                "symbol": symbol,
                "direction": "LONG",
                "state": "ORDER_INTENT",
                "entry_reference": result["reference_next_open"],
                "entry_fill": result["entry_fill"],
                "stop_price": result["stop_price"],
                "stop_fill": result["stop_fill"],
                "target_price": result["target_price"],
                "target_fill_gross": result["target_fill"],
                "gross_rr": result["gross_rr"],
                "net_rr": result["net_rr"],
                "risk_per_unit_quote": result["risk_per_unit_quote"],
                "risk_amount_quote": result["risk_amount_quote"],
                "quantity": result["position_size"],
                "entry_fee_quote": (result["position_size"] or 0) * result.get("entry_fee_per_unit_quote", 0),
                "stop_fee_quote": (result["position_size"] or 0) * result.get("stop_fee_per_unit_quote", 0),
                "target_fee_quote": (result["position_size"] or 0) * result.get("target_fee_per_unit_quote", 0),
                "fill_model": {"components": result.get("fill_components"), "market_inputs": market_inputs},
                "config_snapshot": self.config.as_dict(),
                "data_snapshot_id": result.get("data_snapshot_id"),
                "score": result.get("score"),
                "score_version": self.settings.ifvg_config_version,
                "failed_gates": [],
                "decision_time": now,
                "metadata": {"strategy_id": STRATEGY_ID, "reservation_id": reservation.get("id")},
            })
            validate_transition("ENTRY_ELIGIBLE", "ORDER_INTENT")
            await self.store.create_ifvg_state_event({"setup_id": setup_id, "trade_id": trade.get("id"), "from_state": "ENTRY_ELIGIBLE", "to_state": "ORDER_INTENT", "reason_code": "ATOMIC_RESERVATION_OK", "event_time": now,                     "candle_time": self._iso_time(result.get("signal_candle_time")), "data_snapshot_id": result.get("data_snapshot_id"), "metadata": {"reservation_id": reservation.get("id")}})
            validate_transition("ORDER_INTENT", "ORDER_SUBMITTED")
            trade = await self.store.update_ifvg_trade(trade["id"], {"state": "ORDER_SUBMITTED"}) or trade
            await self.store.create_ifvg_state_event({"setup_id": setup_id, "trade_id": trade.get("id"), "from_state": "ORDER_INTENT", "to_state": "ORDER_SUBMITTED", "reason_code": "PAPER_ORDER_ACCEPTED", "event_time": self._iso_now(), "data_snapshot_id": result.get("data_snapshot_id"), "metadata": {}})
            fill = await self.store.create_ifvg_fill({"trade_id": trade["id"], "fill_role": "ENTRY", "fill_sequence": 1, "reference_price": result["reference_next_open"], "executable_price": result["entry_fill"], "quantity": result["position_size"], "fee_quote": trade.get("entry_fee_quote", 0), "fee_asset": "USDT", "spread_component": result.get("fill_components", {}).get("entry", {}).get("spread", 0), "slippage_component": result.get("fill_components", {}).get("entry", {}).get("slippage", 0), "latency_component": result.get("fill_components", {}).get("entry", {}).get("latency", 0), "event_time": self._iso_now(), "intent_time": now, "execution_time": self._iso_now(), "metadata": {"strategy_id": STRATEGY_ID}})
            validate_transition("ORDER_SUBMITTED", "POSITION_OPEN")
            trade = await self.store.update_ifvg_trade(trade["id"], {"state": "POSITION_OPEN", "opened_at": self._iso_now(), "entry_fill": fill.get("executable_price", result["entry_fill"])}) or trade
            await self.store.update_ifvg_reservation(reservation["id"], {"status": "CONSUMED", "metadata": {"trade_id": trade.get("id")}})
            await self.store.create_ifvg_state_event({"setup_id": setup_id, "trade_id": trade.get("id"), "from_state": "ORDER_SUBMITTED", "to_state": "POSITION_OPEN", "reason_code": "PAPER_ENTRY_FILLED", "event_time": self._iso_now(), "data_snapshot_id": result.get("data_snapshot_id"), "metadata": {"fill_id": fill.get("id")}})
            await self._emit({"type": "ifvg_trade_opened", "trade": trade})
            return trade
        except Exception:
            await self.store.update_ifvg_reservation(reservation["id"], {"status": "RELEASED", "released_at": self._iso_now()})
            raise

    async def manage_exits_once(self) -> int:
        closed = 0
        for trade in await self.store.list_ifvg_trades(state="POSITION_OPEN", user_id=self.settings.ifvg_user_id):
            symbol = str(trade.get("symbol") or "").upper()
            current = (self.market.tickers.get(symbol) or {}).get("price")
            if not symbol or current is None:
                continue
            current = float(current)
            stop = float(trade["stop_price"])
            target = float(trade["target_price"])
            if current > stop and current < target:
                continue
            is_stop = current <= stop
            role = "STOP" if is_stop else "TARGET"
            reference = stop if is_stop else target
            fill_key = "stop_bid" if is_stop else "target_bid"
            market_inputs = {fill_key: current, "bid": current}
            from app.strategies.ifvg.engine import _fill, _fee_per_unit
            executable, components = _fill(reference, role, self.config, market_inputs)
            quantity = float(trade.get("quantity") or 0)
            fee = executable * quantity * self.config.fee_bps / 10000.0
            fill = await self.store.create_ifvg_fill({"trade_id": trade["id"], "fill_role": role, "fill_sequence": 1, "reference_price": reference, "executable_price": executable, "quantity": quantity, "fee_quote": fee, "fee_asset": "USDT", "spread_component": components.get("spread", 0), "slippage_component": components.get("slippage", 0), "latency_component": components.get("latency", 0), "event_time": self._iso_now(), "execution_time": self._iso_now(), "metadata": {"strategy_id": STRATEGY_ID, "intrabar_ambiguous": False}})
            entry = float(trade.get("entry_fill") or trade.get("entry_reference"))
            pnl = (executable - entry) * quantity - fee - float(trade.get("entry_fee_quote") or 0)
            patch = {"state": "STOP_TRIGGERED" if is_stop else "TP_FILLED", "exit_fill": executable, "exit_fee_quote": fee, "realized_pnl_quote": pnl, "closed_at": self._iso_now(), "exit_reason": role, "result": "LOSS" if is_stop else "WIN", "metadata": {"fill_id": fill.get("id"), "strategy_id": STRATEGY_ID}}
            validate_transition("POSITION_OPEN", patch["state"])
            updated = await self.store.update_ifvg_trade(trade["id"], patch)
            await self.store.create_ifvg_state_event({"setup_id": trade.get("setup_id"), "trade_id": trade["id"], "from_state": "POSITION_OPEN", "to_state": patch["state"], "reason_code": "PAPER_EXIT_FILLED", "event_time": self._iso_now(), "metadata": {"fill_id": fill.get("id")}})
            await self.store.update_ifvg_reservation(str((trade.get("metadata") or {}).get("reservation_id")), {"status": "RELEASED", "released_at": self._iso_now()}) if (trade.get("metadata") or {}).get("reservation_id") else None
            await self._emit({"type": "ifvg_trade_closed", "trade": updated or trade})
            closed += 1
        return closed

    async def run_once(self) -> list[dict[str, Any]]:
        started = time.monotonic()
        symbols = list(self.settings.ifvg_symbol_list)
        results = []
        self.last_error = None
        emit(log, logging.INFO, "ifvg_cycle_start", symbols=len(symbols), enabled=getattr(self.settings, "ifvg_enabled", True), paper_only=True)
        if symbols:
            try:
                # Refresh the complete exchangeInfo map at the beginning of every
                # decision cycle. It is reused only inside this cycle.
                await self.market.exchange_filters(symbols[0], force_refresh=True)
                emit(log, logging.INFO, "ifvg_exchange_info_ready", symbols=len(symbols), source=self.market.rest_health().get("last_transport_source"))
            except Exception as exc:
                self.last_error = f"EXCHANGE_INFO_UNAVAILABLE:{type(exc).__name__}"
                log.warning("IFVG cycle skipped: exchangeInfo unavailable: %s", exc)
                results = [{"strategy_id": STRATEGY_ID, "symbol": symbol, "decision": "REJECTED", "primary_rejection_reason": "EXCHANGE_INFO_UNAVAILABLE", "error": type(exc).__name__} for symbol in symbols]
            else:
                try:
                    cycle_clock = await self.market.clock_snapshot(force_refresh=True)
                    emit(log, logging.INFO, "ifvg_clock_ready", valid=cycle_clock.get("valid"), skew_ok=cycle_clock.get("clock_skew_ok"), source=self.market.rest_health().get("last_transport_source"))
                except Exception as exc:
                    self.last_error = f"CLOCK_UNAVAILABLE:{type(exc).__name__}"
                    log.warning("IFVG cycle skipped: Binance clock unavailable: %s", exc)
                    results = [{"strategy_id": STRATEGY_ID, "symbol": symbol, "decision": "REJECTED", "primary_rejection_reason": "CLOCK_UNAVAILABLE", "error": type(exc).__name__} for symbol in symbols]
                    cycle_clock = None
                if cycle_clock is not None:
                    for symbol in symbols:
                        try:
                            scan_result = await self.scan_symbol(symbol, force_exchange_refresh=False, cycle_clock=cycle_clock)
                            results.append(scan_result)
                            emit(log, logging.DEBUG, "ifvg_symbol_result", symbol=symbol, decision=scan_result.get("decision"), rejection=scan_result.get("primary_rejection_reason"))
                        except Exception as exc:
                            log.warning("IFVG scan failed for %s: %s", symbol, exc)
                            emit(log, logging.ERROR, "ifvg_symbol_failure", symbol=symbol, error=exception_text(exc))
                            results.append({"strategy_id": STRATEGY_ID, "symbol": symbol, "decision": "REJECTED", "primary_rejection_reason": "SERVICE_ERROR", "error": type(exc).__name__})
        scan_errors = [str(result.get("error")) for result in results if result.get("error")]
        if scan_errors and self.last_error is None:
            self.last_error = f"SCAN_ERRORS:{scan_errors[0]}"
        self.last_run_at = self._iso_now()
        self.last_run_count = len(results)
        self.last_entry_count = sum(result.get("decision") == "ENTRY_ELIGIBLE" for result in results)
        self.completed_cycles += 1
        emit(log, logging.INFO, "ifvg_cycle_end", duration_ms=round((time.monotonic() - started) * 1000, 1), scanned=len(results), entries=self.last_entry_count, error=self.last_error)
        return results

    async def _scan_loop(self) -> None:
        while True:
            try:
                if not self.store.has_persistent_storage:
                    await self.store.check_persistent_storage()
                if self.store.has_persistent_storage and self.configured:
                    self.status = "RUNNING"
                    await self.run_once()
                else:
                    self.status = "WAITING_CONFIGURATION" if self.store.has_persistent_storage else "WAITING_STORAGE"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.status = "ERROR"
                self.last_error = type(exc).__name__
                log.exception("IFVG scan loop failed")
            await asyncio.sleep(max(10, int(self.settings.ifvg_scan_seconds)))

    async def _exit_loop(self) -> None:
        while True:
            try:
                if self.store.has_persistent_storage:
                    await self.manage_exits_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("IFVG exit loop failed")
            await asyncio.sleep(5)

    async def start(self) -> None:
        if not self.settings.ifvg_enabled:
            self.status = "DISABLED"
            return
        if self.task is None:
            self.task = asyncio.create_task(self._scan_loop())
        if self.exit_task is None:
            self.exit_task = asyncio.create_task(self._exit_loop())

    async def stop(self) -> None:
        tasks = [task for task in (self.task, self.exit_task) if task]
        self.task = None; self.exit_task = None
        for task in tasks: task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.status = "STOPPED"

    def health(self) -> dict[str, Any]:
        return {"strategy_id": STRATEGY_ID, "status": self.status, "enabled": bool(self.settings.ifvg_enabled), "configured": self.configured, "storage": self.store.has_persistent_storage, "symbols": len(self.settings.ifvg_symbol_list), "last_run_at": self.last_run_at, "last_run_count": self.last_run_count, "last_entry_count": self.last_entry_count, "completed_cycles": self.completed_cycles, "last_error": self.last_error, "paper_only": True, "order_endpoints_enabled": False}
