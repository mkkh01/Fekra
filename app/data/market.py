from __future__ import annotations
import asyncio, json, logging, time, urllib.parse, uuid
from collections import defaultdict, deque
from typing import Any
import httpx
import websockets

from app.analysis.safety import closed_candles

log = logging.getLogger("weeg.market")

class MarketData:
    def __init__(self, rest_url: str, ws_url: str, symbols: list[str], analysis_interval: str = "15m", analysis_intervals: list[str] | None = None, ws_api_url: str = "wss://ws-api.binance.com:443/ws-api/v3"):
        configured_urls = [url.strip().rstrip("/") for url in (rest_url or "").split(",") if url.strip()]
        defaults = ["https://api.binance.com", "https://data-api.binance.vision"]
        self.rest_urls = list(dict.fromkeys(defaults + configured_urls))
        self.rest_url = self.rest_urls[0]
        self.ws_api_url = ws_api_url.strip() or "wss://ws-api.binance.com:443/ws-api/v3"
        self.ws_urls = [url.strip().rstrip("/") for url in ws_url.split(",") if url.strip()]
        self.symbols = symbols
        self.analysis_interval = analysis_interval
        self.analysis_intervals = list(dict.fromkeys(analysis_intervals or ["1m", analysis_interval]))
        self.candles: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=500))
        self.tickers: dict[str, dict[str, Any]] = {}
        self._task: asyncio.Task | None = None
        self._socket = None
        self._ws_api_socket = None
        self._ws_api_lock = asyncio.Lock()
        self._listeners: set[asyncio.Queue] = set()
        self.last_event_at: float | None = None
        self.last_ticker_at: dict[str, float] = {}
        self.last_candle_at: dict[tuple[str, str], float] = {}
        self.last_closed_candle_time: dict[tuple[str, str], float] = {}
        self.last_closed_received_at: dict[tuple[str, str], float] = {}
        self.last_candle_source: dict[tuple[str, str], str] = {}
        self.last_error: str | None = None
        self.reconnect_count = 0
        self._rest_pause_until = 0.0
        self._rest_last_error: str | None = None
        self._exchange_info_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._exchange_info_lock = asyncio.Lock()
        self._exchange_info_retry_after = 0.0
        self._exchange_info_last_error: str | None = None
        self._exchange_info_last_success_at: float | None = None
        self._book_ticker_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._clock_cache: tuple[float, dict[str, Any]] | None = None
        self._last_transport_source: str | None = None

    async def load_history(self, symbol: str, interval: str, limit: int = 250) -> list[dict[str, Any]]:
        rows = await self._get_json("/api/v3/klines", {"symbol": symbol.upper(), "interval": interval, "limit": min(limit, 1000)})
        now = time.time()
        result = []
        for r in rows:
            open_time = int(r[0] / 1000)
            close_time = open_time + self._interval_seconds(interval)
            candle = {"time": open_time, "open_time": open_time, "close_time": close_time, "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]), "source_timestamp": close_time}
            candle["closed"] = close_time <= now
            result.append(candle)
        result = [candle for candle in result if candle["closed"]]
        self.candles[(symbol, interval)].clear(); self.candles[(symbol, interval)].extend(result)
        received_at = time.time()
        self.last_candle_at[(symbol, interval)] = received_at
        if result:
            self.last_closed_candle_time[(symbol, interval)] = float(result[-1]["time"])
            self.last_closed_received_at[(symbol, interval)] = received_at
            self.last_candle_source[(symbol, interval)] = self._last_transport_source or "unknown"
        if result and symbol not in self.tickers:
            self.tickers[symbol] = {"symbol": symbol, "price": result[-1]["close"], "change": 0.0, "volume": result[-1]["volume"], "bid": None, "ask": None, "updated_at": result[-1]["source_timestamp"]}
        return result

    async def load_history_window(self, symbol: str, interval: str, days: int = 180) -> list[dict[str, Any]]:
        """Load a reproducible closed-candle window without truncating at the cache size."""
        days = max(1, min(int(days), 365))
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - days * 86400 * 1000
        cursor = start_ms
        raw_rows: list[list[Any]] = []
        while cursor < end_ms:
            payload = await self._get_json("/api/v3/klines", {"symbol": symbol.upper(), "interval": interval, "startTime": cursor, "endTime": end_ms, "limit": 1000})
            if not payload:
                break
            raw_rows.extend(payload)
            last_open = int(payload[-1][0])
            next_cursor = last_open + self._interval_seconds(interval) * 1000
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            if len(payload) < 1000:
                break
            await asyncio.sleep(0.05)
        now = time.time()
        result = []
        for row in raw_rows:
            open_time = int(row[0] / 1000)
            close_time = open_time + self._interval_seconds(interval)
            if close_time > now:
                continue
            result.append({"time": open_time, "open_time": open_time, "close_time": close_time, "open": float(row[1]), "high": float(row[2]), "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]), "source_timestamp": close_time, "closed": True})
        dedup = {int(row["time"]): row for row in result}
        return [dedup[key] for key in sorted(dedup)]

    @staticmethod
    def _interval_seconds(interval: str) -> int:
        units = {"m": 60, "h": 3600, "d": 86400}
        value = str(interval).strip().lower()
        try:
            return int(value[:-1]) * units[value[-1]]
        except (KeyError, TypeError, ValueError):
            return 900

    async def ensure_history(self, symbol: str, interval: str) -> list[dict[str, Any]]:
        symbol = symbol.upper()
        cached = closed_candles(list(self.candles[(symbol, interval)]), interval)
        now = time.time()
        interval_seconds = self._interval_seconds(interval)
        expected_last_open = int(now // interval_seconds) * interval_seconds - interval_seconds
        last_open = int(cached[-1]["time"]) if cached else None
        closed_received_at = self.last_closed_received_at.get((symbol, interval), 0.0)
        cache_fresh = closed_received_at > 0 and now - closed_received_at < max(90, interval_seconds)
        has_current_closed_candle = last_open == expected_last_open
        return cached if len(cached) >= 30 and cache_fresh and has_current_closed_candle else await self.load_history(symbol, interval)

    def ticker_quality_snapshot(self, symbol: str, max_age_seconds: float = 45.0) -> dict[str, Any]:
        received_at = self.last_ticker_at.get(symbol)
        age = None if received_at is None else max(0.0, time.time() - received_at)
        return {
            "symbol": symbol,
            "price_present": self.tickers.get(symbol, {}).get("price") is not None,
            "last_received_at": received_at,
            "age_seconds": round(age, 1) if age is not None else None,
            "max_age_seconds": max_age_seconds,
            "fresh": age is not None and age <= max_age_seconds and self.tickers.get(symbol, {}).get("price") is not None,
            "reason": None if age is not None and age <= max_age_seconds and self.tickers.get(symbol, {}).get("price") is not None else "STALE_OR_MISSING_TICKER",
        }

    def data_quality_snapshot(self, symbol: str, interval: str, max_age_multiplier: float = 2.0) -> dict[str, Any]:
        now = time.time()
        rows = closed_candles(list(self.candles[(symbol, interval)]), interval)
        interval_seconds = self._interval_seconds(interval)
        last_time = float(rows[-1]["time"]) if rows else None
        candle_age = None if last_time is None else max(0.0, now - last_time - interval_seconds)
        last_received = self.last_closed_received_at.get((symbol, interval))
        receive_age = None if last_received is None else max(0.0, now - last_received)
        max_age = interval_seconds * max_age_multiplier
        fresh = bool(rows) and candle_age is not None and candle_age <= max_age
        history_ready = len(rows) >= 30
        return {
            "symbol": symbol,
            "interval": interval,
            "rows": len(rows),
            "history_ready": history_ready,
            "last_closed_candle_time": last_time,
            "last_closed_received_at": last_received,
            "source": self.last_candle_source.get((symbol, interval)),
            "candle_age_seconds": round(candle_age, 1) if candle_age is not None else None,
            "receive_age_seconds": round(receive_age, 1) if receive_age is not None else None,
            "max_age_seconds": max_age,
            "fresh": fresh and history_ready,
            "reason": None if fresh and history_ready else ("INSUFFICIENT_HISTORY" if not history_ready else "STALE_CLOSED_CANDLE"),
        }

    def current_open_candle(self, symbol: str, interval: str) -> dict[str, Any] | None:
        symbol = symbol.upper()
        cache = self.candles[(symbol, interval)]
        if not cache:
            return None
        row = dict(cache[-1])
        if row.get("closed") is not False:
            return None
        row["received_at"] = self.last_candle_at.get((symbol, interval))
        return row

    def decision_data_quality_snapshot(self, symbol: str, interval: str) -> dict[str, Any]:
        snapshot = self.data_quality_snapshot(symbol, interval)
        seconds = self._interval_seconds(interval)
        now = time.time()
        expected_last_open = int(now // seconds) * seconds - seconds
        last_open = snapshot.get("last_closed_candle_time")
        current = last_open is not None and int(last_open) == expected_last_open
        received_at = snapshot.get("last_closed_received_at")
        received_fresh = received_at is not None and now - float(received_at) <= max(90.0, 2.0 * float(seconds))
        snapshot["expected_last_closed_candle_time"] = expected_last_open
        snapshot["strict_current_closed_candle"] = current
        snapshot["strict_received_fresh"] = received_fresh
        snapshot["fresh"] = bool(snapshot.get("fresh") and current and received_fresh)
        if not snapshot["fresh"]:
            snapshot["reason"] = "STALE_OR_MISSING_DECISION_DATA"
        return snapshot

    def data_integrity_snapshot(self, symbol: str, interval: str) -> dict[str, Any]:
        rows = closed_candles(list(self.candles[(symbol, interval)]), interval)
        expected = self._interval_seconds(interval)
        gaps = []
        duplicates = []
        seen = set()
        for row in rows:
            timestamp = int(row["time"])
            if timestamp in seen:
                duplicates.append(timestamp)
            seen.add(timestamp)
        for previous, current in zip(rows, rows[1:]):
            delta = int(current["time"]) - int(previous["time"])
            if delta != expected:
                gaps.append({"from": previous["time"], "to": current["time"], "missing_candles": max(0, delta // expected - 1)})
        malformed = [row.get("time") for row in rows if any(row.get(key) is None for key in ("open", "high", "low", "close", "volume"))]
        return {"symbol": symbol, "interval": interval, "gaps": gaps, "duplicates": duplicates, "malformed": malformed, "valid": not gaps and not duplicates and not malformed}

    def data_quality_vetoes(self, symbol: str, intervals: tuple[str, ...] = ("4h", "1h", "15m")) -> list[str]:
        vetoes = []
        for interval in intervals:
            snapshot = self.data_quality_snapshot(symbol, interval)
            integrity = self.data_integrity_snapshot(symbol, interval)
            if not snapshot["fresh"]:
                age = snapshot["candle_age_seconds"]
                age_text = "غير متوفرة" if age is None else f"قديمة {age:.0f} ثانية"
                vetoes.append(f"بيانات {interval} لـ{symbol} غير صالحة: {age_text}")
            if not integrity["valid"]:
                vetoes.append(f"فجوة أو تكرار في بيانات {interval} لـ{symbol}")
        return vetoes

    def health_snapshot(self) -> dict[str, Any]:
        now = time.time()
        event_age = None if self.last_event_at is None else round(max(0.0, now - self.last_event_at), 1)
        live = self._task is not None and event_age is not None and event_age < 45
        symbol_health = {}
        for symbol in self.symbols:
            ticker_quality = self.ticker_quality_snapshot(symbol)
            symbol_health[symbol] = {
                "ticker_age_seconds": ticker_quality["age_seconds"],
                "ticker_fresh": ticker_quality["fresh"],
                "ticker": ticker_quality,
                "intervals": {interval: self.data_quality_snapshot(symbol, interval) for interval in self.analysis_intervals},
            }
        return {
            "live_feed": live,
            "all_symbols_live": bool(symbol_health) and all(item["ticker_fresh"] for item in symbol_health.values()),
            "last_event_at": self.last_event_at,
            "last_event_age_seconds": event_age,
            "last_error": self.last_error,
            "reconnect_count": self.reconnect_count,
            "exchange_info": {
                "cached_symbols": len(self._exchange_info_cache),
                "last_success_at": self._exchange_info_last_success_at,
                "last_error": self._exchange_info_last_error,
                "retry_after_seconds": max(0, round(self._exchange_info_retry_after - now, 1)),
                "fresh_for_cycle": self._exchange_info_last_success_at is not None and now - self._exchange_info_last_success_at <= 90,
            },
            "symbols": symbol_health,
        }

    def rest_health(self) -> dict[str, Any]:
        now = time.time()
        return {
            "available": now >= self._rest_pause_until,
            "retry_after_seconds": max(0, round(self._rest_pause_until - now, 1)),
            "last_error": self._rest_last_error,
            "last_transport_source": self._last_transport_source,
            "ws_api_url": self.ws_api_url,
        }

    @staticmethod
    def _ws_method(path: str) -> str | None:
        return {
            "/api/v3/time": "time",
            "/api/v3/exchangeInfo": "exchangeInfo",
            "/api/v3/klines": "klines",
            "/api/v3/ticker/bookTicker": "ticker.book",
        }.get(path)

    async def _ws_api_json(self, path: str, params: dict[str, Any]) -> Any:
        method = self._ws_method(path)
        if method is None:
            raise RuntimeError(f"NO_WS_METHOD:{path}")
        request_id = str(uuid.uuid4())
        request = {"id": request_id, "method": method, "params": {**params, "returnRateLimits": False}}
        async with self._ws_api_lock:
            socket = self._ws_api_socket
            try:
                if socket is None or getattr(socket, "closed", False):
                    socket = await websockets.connect(
                        self.ws_api_url,
                        ping_interval=20,
                        ping_timeout=20,
                        close_timeout=5,
                        open_timeout=15,
                    )
                    self._ws_api_socket = socket
                await socket.send(json.dumps(request, separators=(",", ":")))
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    remaining = max(0.1, deadline - time.monotonic())
                    message = json.loads(await asyncio.wait_for(socket.recv(), timeout=remaining))
                    if str(message.get("id")) != request_id:
                        continue
                    status = int(message.get("status", 500))
                    if status != 200:
                        error = message.get("error") or {}
                        raise RuntimeError(
                            f"BINANCE_WS_API_STATUS:{status}:{error.get('code', 'unknown')}:{error.get('msg', 'unknown')}"
                        )
                    result = message.get("result")
                    if result is None:
                        raise RuntimeError("BINANCE_WS_API_EMPTY_RESULT")
                    self._last_transport_source = "websocket_api"
                    self._rest_last_error = None
                    return result
                raise RuntimeError("BINANCE_WS_API_TIMEOUT")
            except Exception:
                if socket is not None:
                    try:
                        await socket.close()
                    except Exception:
                        pass
                self._ws_api_socket = None
                raise

    async def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        ws_error: Exception | None = None
        if self._ws_method(path) is not None:
            try:
                result = await self._ws_api_json(path, params)
                self._last_transport_source = "websocket_api"
                return result
            except Exception as exc:
                ws_error = exc
                self._rest_last_error = f"WS_API:{type(exc).__name__}"

        now = time.time()
        if now < self._rest_pause_until:
            retry = max(1, int(self._rest_pause_until - now))
            raise RuntimeError(f"BINANCE_REST_COOLDOWN:{retry}s") from ws_error
        errors: list[Exception] = [ws_error] if ws_error else []
        rate_limited = False
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0 Weeg/1.0", "Accept": "application/json"}) as client:
            for base_url in self.rest_urls:
                try:
                    response = await client.get(f"{base_url}{path}", params=params)
                    response.raise_for_status()
                    self.rest_url = base_url
                    self._last_transport_source = f"rest:{base_url}"
                    self._rest_last_error = None
                    return response.json()
                except httpx.HTTPStatusError as exc:
                    errors.append(exc)
                    status = exc.response.status_code
                    if status in {418, 429}:
                        rate_limited = True
                        continue
                    if 500 <= status < 600:
                        continue
                    raise
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    errors.append(exc)
                    continue
        if rate_limited:
            self._rest_pause_until = time.time() + 60
            self._rest_last_error = "ALL_ENDPOINTS_RATE_LIMITED"
            raise RuntimeError("BINANCE_REST_RATE_LIMITED:all endpoints") from errors[-1]
        self._rest_pause_until = time.time() + 15
        self._rest_last_error = type(errors[-1]).__name__ if errors else "NO_ENDPOINTS"
        raise RuntimeError(f"BINANCE_REST_UNAVAILABLE:{self._rest_last_error}") from (errors[-1] if errors else None)

    async def clock_snapshot(self, force_refresh: bool = False) -> dict[str, Any]:
        cached = self._clock_cache
        if not force_refresh and cached and time.time() - cached[0] < 10:
            return dict(cached[1])
        server_time = time.time()
        payload = await self._get_json("/api/v3/time", {})
        exchange_time = float(payload.get("serverTime", 0)) / 1000.0
        observed = time.time()
        local_mid = (server_time + observed) / 2.0
        snapshot = {"server_time_utc": observed, "exchange_time_utc": exchange_time, "clock_offset_ms": (exchange_time - local_mid) * 1000.0, "clock_skew_limit_ms": 1000, "valid": exchange_time > 0}
        snapshot["clock_skew_ok"] = snapshot["valid"] and abs(snapshot["clock_offset_ms"]) <= snapshot["clock_skew_limit_ms"]
        self._clock_cache = (time.time(), snapshot)
        return dict(snapshot)

    @staticmethod
    def _normalize_exchange_symbol(item: dict[str, Any]) -> dict[str, Any]:
        filters = {row.get("filterType"): row for row in item.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER", {})
        lot_filter = filters.get("LOT_SIZE", {})
        notional_filter = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
        return {
            "symbol": item.get("symbol"),
            "status": item.get("status"),
            "base_asset": item.get("baseAsset"),
            "quote_asset": item.get("quoteAsset"),
            "tick_size": float(price_filter.get("tickSize", 0) or 0),
            "min_price": float(price_filter.get("minPrice", 0) or 0),
            "max_price": float(price_filter.get("maxPrice", 0) or 0),
            "step_size": float(lot_filter.get("stepSize", 0) or 0),
            "min_qty": float(lot_filter.get("minQty", 0) or 0),
            "max_qty": float(lot_filter.get("maxQty", 0) or 0),
            "min_notional": float(notional_filter.get("minNotional", 0) or 0),
            "apply_min_to_market": notional_filter.get("applyMinToMarket"),
            "raw_filters": filters,
        }

    async def exchange_filters(self, symbol: str, force_refresh: bool = False) -> dict[str, Any]:
        symbol = symbol.upper()
        now = time.time()
        cached = self._exchange_info_cache.get(symbol)
        if not force_refresh and cached and now - cached[0] < 3600:
            return dict(cached[1])
        if now < self._exchange_info_retry_after:
            retry = max(1, int(self._exchange_info_retry_after - now))
            raise RuntimeError(f"exchangeInfo temporarily unavailable; retry in {retry}s")
        async with self._exchange_info_lock:
            now = time.time()
            cached = self._exchange_info_cache.get(symbol)
            if not force_refresh and cached and now - cached[0] < 3600:
                return dict(cached[1])
            if now < self._exchange_info_retry_after:
                retry = max(1, int(self._exchange_info_retry_after - now))
                raise RuntimeError(f"exchangeInfo temporarily unavailable; retry in {retry}s")
            try:
                # Load the complete symbol map once. Per-symbol exchangeInfo requests
                # multiply IP load and turn a transient 418 into a 20-symbol failure storm.
                payload = await self._get_json("/api/v3/exchangeInfo", {})
                received_at = time.time()
                normalized = {
                    str(item.get("symbol")): self._normalize_exchange_symbol(item)
                    for item in payload.get("symbols", [])
                    if item.get("symbol")
                }
                if not normalized:
                    raise RuntimeError("exchangeInfo returned no symbols")
                for item_symbol, item_filters in normalized.items():
                    item_filters["observed_at"] = received_at
                    self._exchange_info_cache[item_symbol] = (received_at, item_filters)
                self._exchange_info_retry_after = 0.0
                self._exchange_info_last_error = None
                self._exchange_info_last_success_at = received_at
            except Exception as exc:
                # Do not retry per symbol. The caller will receive a bounded, shared
                # failure until cooldown expires, while any last-known cache remains usable.
                self._exchange_info_last_error = f"{type(exc).__name__}: {exc}"
                self._exchange_info_retry_after = time.time() + 60
                raise RuntimeError(f"exchangeInfo unavailable: {type(exc).__name__}") from exc
        cached = self._exchange_info_cache.get(symbol)
        if not cached:
            raise RuntimeError(f"exchangeInfo missing for {symbol}")
        return dict(cached[1])

    async def book_ticker(self, symbol: str, force_refresh: bool = False) -> dict[str, Any]:
        symbol = symbol.upper()
        cached = self._book_ticker_cache.get(symbol)
        # A stream update is a fresh receipt, including when the caller asks for a
        # forced refresh. It avoids one request per symbol while retaining a strict
        # age gate at the decision layer.
        if cached and cached[1].get("source") == "websocket_stream" and time.time() - cached[0] <= 10:
            return dict(cached[1])
        if not force_refresh and cached and time.time() - cached[0] <= 10:
            return dict(cached[1])
        payload = await self._get_json("/api/v3/ticker/bookTicker", {"symbol": symbol})
        if isinstance(payload, list):
            payload = next((item for item in payload if str(item.get("symbol", "")).upper() == symbol), None)
        if not isinstance(payload, dict):
            raise RuntimeError(f"bookTicker missing for {symbol}")
        received_at = time.time()
        normalized = {"symbol": symbol, "bid": float(payload.get("bidPrice", 0) or 0) or None, "ask": float(payload.get("askPrice", 0) or 0) or None, "bid_qty": float(payload.get("bidQty", 0) or 0), "ask_qty": float(payload.get("askQty", 0) or 0), "observed_at": received_at, "source": self._last_transport_source or "unknown"}
        if normalized["bid"] is None or normalized["ask"] is None:
            raise RuntimeError(f"bookTicker incomplete for {symbol}")
        self._book_ticker_cache[symbol] = (received_at, normalized)
        return dict(normalized)

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
        task = self._task
        self._task = None
        socket = self._socket
        api_socket = self._ws_api_socket
        for active_socket in (socket, api_socket):
            if active_socket is not None:
                try:
                    await asyncio.wait_for(active_socket.close(), timeout=2)
                except Exception:
                    pass
        self._socket = None
        self._ws_api_socket = None
        if task:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=3)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                if not task.done():
                    task.cancel()

    async def _run(self):
        streams = "/".join([*(f"{s.lower()}@kline_{interval}" for s in self.symbols for interval in self.analysis_intervals), *(f"{s.lower()}@ticker" for s in self.symbols), *(f"{s.lower()}@bookTicker" for s in self.symbols)])
        delay = 1
        candidate_index = 0
        while True:
            try:
                if not self.ws_urls:
                    raise RuntimeError("no Binance WebSocket URL configured")
                base_url = self.ws_urls[candidate_index % len(self.ws_urls)]
                url = f"{base_url}?streams={streams}"
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5, open_timeout=15) as socket:
                    self._socket = socket
                    delay = 1
                    async for raw in socket:
                        payload = json.loads(raw); data = payload.get("data", {})
                        event_type = data.get("e")
                        self.last_event_at = time.time()
                        self.last_error = None
                        if event_type == "bookTicker":
                            symbol = str(data.get("s", "")).upper()
                            if not symbol or not data.get("b") or not data.get("a"):
                                continue
                            received_at = time.time()
                            book = {"symbol": symbol, "bid": float(data["b"]), "ask": float(data["a"]), "bid_qty": float(data.get("B", 0) or 0), "ask_qty": float(data.get("A", 0) or 0), "observed_at": received_at, "source": "websocket_stream"}
                            self._book_ticker_cache[symbol] = (received_at, book)
                            ticker = self.tickers.get(symbol, {"symbol": symbol, "price": None, "change": 0.0, "volume": 0.0, "updated_at": int(data.get("u", 0))})
                            self.tickers[symbol] = {**ticker, "bid": book["bid"], "ask": book["ask"], "updated_at": int(data.get("u", 0))}
                            await self._broadcast({"type": "bookTicker", "symbol": symbol, "book_ticker": book})
                            continue
                        if event_type == "24hrTicker":
                            symbol = data.get("s")
                            if not symbol: continue
                            previous = self.tickers.get(symbol, {}).get("price")
                            price = float(data["c"])
                            change = float(data.get("P", 0.0))
                            self.tickers[symbol] = {"symbol": symbol, "price": price, "change": change, "volume": float(data.get("v", 0.0)), "bid": float(data.get("b", 0.0) or 0.0) or None, "ask": float(data.get("a", 0.0) or 0.0) or None, "updated_at": int(data.get("E", 0) / 1000)}
                            self.last_ticker_at[symbol] = time.time()
                            await self._broadcast({"type": "ticker", "symbol": symbol, "price": price, "ticker": self.tickers[symbol]})
                            continue
                        k = data.get("k")
                        if not k: continue
                        symbol, interval = k["s"], k["i"]
                        open_time = int(k["t"] / 1000)
                        close_time = int(k["T"] / 1000)
                        candle = {"time": open_time, "open_time": open_time, "close_time": close_time, "open": float(k["o"]), "high": float(k["h"]), "low": float(k["l"]), "close": float(k["c"]), "volume": float(k["v"]), "source_timestamp": int(data.get("E", 0) / 1000), "closed": bool(k["x"])}
                        cache = self.candles[(symbol, interval)]
                        received_at = time.time()
                        self.last_candle_at[(symbol, interval)] = received_at
                        if cache and cache[-1]["time"] == candle["time"]: cache[-1] = candle
                        else: cache.append(candle)
                        if candle["closed"]:
                            self.last_closed_candle_time[(symbol, interval)] = float(candle["time"])
                            self.last_closed_received_at[(symbol, interval)] = received_at
                            self.last_candle_source[(symbol, interval)] = "websocket"
                        ticker = self.tickers.get(symbol, {})
                        if not ticker:
                            previous = cache[-2]["close"] if len(cache) > 1 else candle["close"]
                            ticker = {"symbol": symbol, "price": candle["close"], "change": (candle["close"] - previous) / max(previous, 1e-9) * 100, "volume": candle["volume"], "bid": None, "ask": None, "updated_at": candle["source_timestamp"]}
                            self.tickers[symbol] = ticker
                        else:
                            ticker = {**ticker, "volume": candle["volume"]}
                            self.tickers[symbol] = ticker
                        await self._broadcast({"type": "candle", "symbol": symbol, "interval": interval, "candle": candle, "ticker": ticker})
                    self._socket = None
            except asyncio.CancelledError:
                self._socket = None
                raise

            except Exception as exc:
                self.last_error = str(exc)
                self.reconnect_count += 1
                candidate_index += 1
                log.warning("market websocket reconnect via candidate %s: %s", candidate_index, exc)
                await asyncio.sleep(delay); delay = min(delay * 2, 30)
