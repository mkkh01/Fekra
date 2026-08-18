from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
import websockets

from app.config.settings import get_settings
from app.state import RuntimeState
from app.storage.store import StorageManager

logger = logging.getLogger(__name__)


class MarketService:
    symbols = [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
        "TRXUSDT", "LTCUSDT", "BCHUSDT", "SUIUSDT", "NEARUSDT",
        "APTUSDT", "TONUSDT", "XLMUSDT", "HBARUSDT", "ICPUSDT",
    ]
    timeframes = ["5m", "15m", "1h", "4h", "1d"]

    def __init__(self, state: RuntimeState, storage: StorageManager | None = None) -> None:
        self.state = state
        self.storage = storage
        self._last_cache: dict[str, float] = {}
        self.task: asyncio.Task | None = None
        self.initial_task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self.initial_task = asyncio.create_task(self.load_initial_tickers(), name="binance-initial-snapshot")
        self.task = asyncio.create_task(self._run(), name="binance-market-stream")

    async def stop(self) -> None:
        self._stop.set()
        for task in (self.initial_task, self.task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def candles(self, symbol: str, interval: str = "5m", limit: int = 100) -> list[dict[str, Any]]:
        url = "https://api.binance.com/api/v3/klines"
        params = {"symbol": symbol.upper(), "interval": interval, "limit": min(max(limit, 10), 500)}
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            rows = response.json()
        return [
            {
                "open_time": row[0],
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_time": row[6],
            }
            for row in rows
        ]

    async def load_initial_tickers(self) -> None:
        url = "https://api.binance.com/api/v3/ticker/24hr"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url)
                response.raise_for_status()
                rows = response.json()
            requested = set(self.symbols)
            for row in rows:
                symbol = row.get("symbol")
                if symbol in requested:
                    self.state.update_ticker(symbol, self._normalize_ticker(row))
            self.state.event("MARKET", "Initial Binance ticker snapshot loaded", {"count": len(self.state.tickers)})
        except Exception as exc:
            logger.warning("Initial ticker snapshot failed: %s", exc)
            self.state.event("SYSTEM", "Initial market snapshot unavailable", {"error": str(exc)})

    async def _run(self) -> None:
        streams = "/".join(f"{symbol.lower()}@ticker" for symbol in self.symbols)
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"
        delay = get_settings().market_reconnect_seconds
        while not self._stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5) as socket:
                    self.state.market_connected = True
                    self.state.event("MARKET", "Binance WebSocket connected")
                    delay = get_settings().market_reconnect_seconds
                    async for raw in socket:
                        message = json.loads(raw)
                        payload = message.get("data", message)
                        symbol = payload.get("s")
                        if symbol:
                            normalized = self._normalize_ticker(payload)
                            self.state.update_ticker(symbol, normalized)
                            if self.storage is not None and time.monotonic() - self._last_cache.get(symbol, 0) >= 5:
                                self._last_cache[symbol] = time.monotonic()
                                asyncio.create_task(self.storage.cache_snapshot(f"market:ticker:{symbol}", normalized, ttl=30))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state.market_connected = False
                self.state.event("SYSTEM", "Binance WebSocket disconnected; retrying", {"error": str(exc), "retry_seconds": delay})
                await asyncio.sleep(delay)
                delay = min(delay * 2, 120)

    @staticmethod
    def _normalize_ticker(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "price": float(row.get("c", 0) or 0),
            "change_24h_pct": float(row.get("P", 0) or 0),
            "volume_24h": float(row.get("v", 0) or 0),
            "high_24h": float(row.get("h", 0) or 0),
            "low_24h": float(row.get("l", 0) or 0),
            "event_time": row.get("E"),
            "status": "LIVE",
        }
