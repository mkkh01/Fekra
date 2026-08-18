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
            "gemini_usage": self.gemini_usage,
        }
