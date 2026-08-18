from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any


UTC = timezone.utc


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class RuntimeState:
    def __init__(self) -> None:
        self.started_at = utc_now()
        self.brain_status = "SLEEPING"
        self.kill_switch = False
        self.last_market_message: str | None = None
        self.last_news_message: str | None = None
        self.market_connected = False
        self.redis_connected = False
        self.supabase_configured = False
        self.gemini_configured = False
        self.tickers: dict[str, dict[str, Any]] = {}
        self.news: deque[dict[str, Any]] = deque(maxlen=200)
        self.events: deque[dict[str, Any]] = deque(maxlen=300)
        self.cycles: deque[dict[str, Any]] = deque(maxlen=100)
        self.positions: list[dict[str, Any]] = []
        self.decision_performance: list[dict[str, Any]] = []
        self.gemini_usage: dict[str, Any] = {
            "configured_keys": 0,
            "active_key_index": None,
            "requests_total": 0,
            "success_total": 0,
            "failures_total": 0,
            "rotations_total": 0,
            "keys": [],
        }

    def event(self, event_type: str, message: str, data: dict[str, Any] | None = None) -> None:
        item = {
            "timestamp": utc_now(),
            "type": event_type,
            "message": message,
            "data": data or {},
        }
        self.events.appendleft(item)

    def update_ticker(self, symbol: str, payload: dict[str, Any]) -> None:
        self.tickers[symbol] = {**payload, "symbol": symbol, "updated_at": utc_now()}
        self.last_market_message = utc_now()

    def add_news(self, item: dict[str, Any]) -> None:
        self.news.appendleft(item)
        self.last_news_message = utc_now()

    def add_cycle(self, item: dict[str, Any]) -> None:
        self.cycles.appendleft(item)

    def evaluate_decision_outcomes(self, symbol: str, current_price: float) -> list[dict[str, Any]]:
        updates: list[dict[str, Any]] = []
        if not current_price or current_price <= 0:
            return updates
        for cycle in list(self.cycles):
            if cycle.get("trigger_symbol") != symbol or cycle.get("performance"):
                continue
            reference = float(((cycle.get("inputs") or {}).get("market_observation") or {}).get("price") or 0)
            if not reference or reference <= 0:
                continue
            change_pct = round((current_price - reference) / reference * 100, 4)
            decision = cycle.get("final_decision", "WAIT")
            if decision == "WAIT":
                label = "MISSED_OPPORTUNITY" if abs(change_pct) >= 3 else "CORRECT_WAIT" if abs(change_pct) < 1 else "WAIT_UNRESOLVED"
            elif decision == "LONG_READY":
                label = "FALSE_LONG" if change_pct <= -2 else "CORRECT_LONG" if change_pct >= 2 else "LONG_UNRESOLVED"
            elif decision == "SHORT_READY":
                label = "FALSE_SHORT" if change_pct >= 2 else "CORRECT_SHORT" if change_pct <= -2 else "SHORT_UNRESOLVED"
            else:
                label = "UNRESOLVED"
            performance = {"status": label, "reference_price": reference, "latest_price": current_price, "change_pct": change_pct, "evaluated_at": utc_now()}
            cycle["performance"] = performance
            cycle["inputs"] = {**(cycle.get("inputs") or {}), "performance": performance}
            updates.append(cycle)
            self.decision_performance.insert(0, {"cycle_id": cycle.get("id"), "symbol": symbol, "decision": decision, **performance})
        self.decision_performance = self.decision_performance[:100]
        return updates

    def performance_summary(self) -> dict[str, Any]:
        completed = [item for item in self.decision_performance if item.get("status", "").startswith(("CORRECT_", "FALSE_", "MISSED_"))]
        correct = sum(1 for item in completed if item.get("status", "").startswith("CORRECT_"))
        false = sum(1 for item in completed if item.get("status", "").startswith("FALSE_"))
        missed = sum(1 for item in completed if item.get("status") == "MISSED_OPPORTUNITY")
        return {"evaluated": len(completed), "correct": correct, "false": false, "missed_opportunities": missed, "accuracy_pct": round(correct / len(completed) * 100, 2) if completed else None, "recent": self.decision_performance[:20]}

    def health(self) -> dict[str, Any]:
        return {
            "app": "ok",
            "started_at": self.started_at,
            "trading_mode": "PAPER",
            "brain_status": self.brain_status,
            "kill_switch": self.kill_switch,
            "market_connected": self.market_connected,
            "redis_connected": self.redis_connected,
            "supabase_configured": self.supabase_configured,
            "gemini_configured": self.gemini_configured,
            "last_market_message": self.last_market_message,
            "last_news_message": self.last_news_message,
        }

    def dashboard_snapshot(self) -> dict[str, Any]:
        return {
            "health": self.health(),
            "tickers": list(self.tickers.values()),
            "news": list(self.news)[:30],
            "events": list(self.events)[:50],
            "cycles": list(self.cycles)[:20],
            "positions": self.positions,
            "performance": self.performance_summary(),
            "gemini_usage": self.gemini_usage,
        }
