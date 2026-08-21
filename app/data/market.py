from __future__ import annotations
import asyncio, json, logging, time, urllib.parse
from collections import defaultdict, deque
from typing import Any
import httpx
import websockets

log = logging.getLogger("weeg.market")

class MarketData:
    def __init__(self, rest_url: str, ws_url: str, symbols: list[str], analysis_interval: str = "15m", analysis_intervals: list[str] | None = None):
        self.rest_url = rest_url.rstrip("/")
        self.ws_urls = [url.strip().rstrip("/") for url in ws_url.split(",") if url.strip()]
        self.symbols = symbols
        self.analysis_interval = analysis_interval
        self.analysis_intervals = list(dict.fromkeys(analysis_intervals or ["1m", analysis_interval]))
        self.candles: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=500))
        self.tickers: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task | None = None
        self._listeners: set[asyncio.Queue] = set()
        self.last_event_at: float | None = None
        self.last_ticker_at: dict[str, float] = {}
        self.last_candle_at: dict[tuple[str, str], float] = {}
        self.last_error: str | None = None
        self.reconnect_count = 0

    async def load_history(self, symbol: str, interval: str, limit: int = 250) -> list[dict[str, Any]]:
        url = f"{self.rest_url}/api/v3/klines"
        params = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": min(limit, 1000)})
        try:
            async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0 Weeg/1.0", "Accept": "application/json"}) as client:
                response = await client.get(url, params={"symbol": symbol, "interval": interval, "limit": min(limit, 1000)})
                response.raise_for_status(); rows = response.json()
        except Exception:
            process = await asyncio.create_subprocess_exec("curl", "-sSfL", "-A", "Mozilla/5.0 Weeg/1.0", f"{url}?{params}", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await process.communicate()
            if process.returncode != 0: raise RuntimeError(stderr.decode(errors="ignore")[:240])
            rows = json.loads(stdout.decode())
        result = [{"time": int(r[0] / 1000), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]), "closed": True} for r in rows]
        self.candles[(symbol, interval)].clear(); self.candles[(symbol, interval)].extend(result)
        self.last_candle_at[(symbol, interval)] = time.time()
        if result and symbol not in self.tickers:
            self.tickers[symbol] = {"symbol": symbol, "price": result[-1]["close"], "change": 0.0, "volume": result[-1]["volume"], "updated_at": result[-1]["time"]}
        return result

    async def ensure_history(self, symbol: str, interval: str) -> list[dict[str, Any]]:
        cached = list(self.candles[(symbol, interval)])
        updated_at = self.last_candle_at.get((symbol, interval), 0.0)
        cache_fresh = updated_at > 0 and time.time() - updated_at < 90
        return cached if len(cached) >= 30 and cache_fresh else await self.load_history(symbol, interval)

    def health_snapshot(self) -> dict[str, Any]:
        now = time.time()
        event_age = None if self.last_event_at is None else round(max(0.0, now - self.last_event_at), 1)
        live = self._task is not None and event_age is not None and event_age < 45
        return {
            "live_feed": live,
            "last_event_at": self.last_event_at,
            "last_event_age_seconds": event_age,
            "last_error": self.last_error,
            "reconnect_count": self.reconnect_count,
        }

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._listeners.add(queue); return queue

    def unsubscribe(self, queue: asyncio.Queue): self._listeners.discard(queue)

    async def _broadcast(self, event: dict[str, Any]):
        for queue in list(self._listeners):
            try: queue.put_nowait(event)
            except asyncio.QueueFull: pass

    async def start(self):
        if self._task is None: self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task: self._task.cancel(); await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self):
        streams = "/".join([*(f"{s.lower()}@kline_{interval}" for s in self.symbols for interval in self.analysis_intervals), *(f"{s.lower()}@ticker" for s in self.symbols)])
        delay = 1
        candidate_index = 0
        while True:
            try:
                if not self.ws_urls:
                    raise RuntimeError("no Binance WebSocket URL configured")
                base_url = self.ws_urls[candidate_index % len(self.ws_urls)]
                url = f"{base_url}?streams={streams}"
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5, open_timeout=15) as socket:
                    delay = 1
                    async for raw in socket:
                        payload = json.loads(raw); data = payload.get("data", {})
                        event_type = data.get("e")
                        self.last_event_at = time.time()
                        self.last_error = None
                        if event_type == "24hrTicker":
                            symbol = data.get("s")
                            if not symbol: continue
                            previous = self.tickers.get(symbol, {}).get("price")
                            price = float(data["c"])
                            change = float(data.get("P", 0.0))
                            self.tickers[symbol] = {"symbol": symbol, "price": price, "change": change, "volume": float(data.get("v", 0.0)), "updated_at": int(data.get("E", 0) / 1000)}
                            self.last_ticker_at[symbol] = time.time()
                            await self._broadcast({"type": "ticker", "symbol": symbol, "price": price, "ticker": self.tickers[symbol]})
                            continue
                        k = data.get("k")
                        if not k: continue
                        symbol, interval = k["s"], k["i"]
                        candle = {"time": int(k["t"] / 1000), "open": float(k["o"]), "high": float(k["h"]), "low": float(k["l"]), "close": float(k["c"]), "volume": float(k["v"]), "closed": bool(k["x"])}
                        cache = self.candles[(symbol, interval)]
                        self.last_candle_at[(symbol, interval)] = time.time()
                        if cache and cache[-1]["time"] == candle["time"]: cache[-1] = candle
                        else: cache.append(candle)
                        ticker = self.tickers.get(symbol, {})
                        if not ticker:
                            previous = cache[-2]["close"] if len(cache) > 1 else candle["close"]
                            ticker = {"symbol": symbol, "price": candle["close"], "change": (candle["close"] - previous) / max(previous, 1e-9) * 100, "volume": candle["volume"], "updated_at": candle["time"]}
                            self.tickers[symbol] = ticker
                        else:
                            ticker = {**ticker, "volume": candle["volume"]}
                            self.tickers[symbol] = ticker
                        await self._broadcast({"type": "candle", "symbol": symbol, "interval": interval, "candle": candle, "ticker": ticker})
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.last_error = str(exc)
                self.reconnect_count += 1
                candidate_index += 1
                log.warning("market websocket reconnect via candidate %s: %s", candidate_index, exc)
                await asyncio.sleep(delay); delay = min(delay * 2, 30)
