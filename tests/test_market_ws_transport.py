from __future__ import annotations

import asyncio
import time

from app.data.market import MarketData


def test_book_ticker_force_refresh_does_not_use_old_api_cache(monkeypatch):
    async def scenario():
        market = MarketData("https://example.invalid", "wss://example.invalid", ["BTCUSDT"])
        market._book_ticker_cache["BTCUSDT"] = (0.0, {"symbol": "BTCUSDT", "bid": 1.0, "ask": 1.1, "observed_at": 0.0, "source": "websocket_api"})
        async def fresh_ws(path, params):
            assert path == "/api/v3/ticker/bookTicker"
            assert params == {"symbol": "BTCUSDT"}
            return {"symbol": "BTCUSDT", "bidPrice": "2.0", "askPrice": "2.1", "bidQty": "3", "askQty": "4"}
        monkeypatch.setattr(market, "_ws_api_json", fresh_ws)
        result = await market.book_ticker("BTCUSDT", force_refresh=True)
        assert result["bid"] == 2.0
        assert result["ask"] == 2.1
        assert result["source"] == "websocket_api"

    asyncio.run(scenario())


def test_book_ticker_stream_is_accepted_without_rest(monkeypatch):
    async def scenario():
        market = MarketData("https://example.invalid", "wss://example.invalid", ["BTCUSDT"])
        received_at = time.time()
        market._book_ticker_cache["BTCUSDT"] = (received_at, {"symbol": "BTCUSDT", "bid": 10.0, "ask": 10.1, "observed_at": received_at, "source": "websocket_stream"})
        async def should_not_run(path, params):
            raise AssertionError("stream cache should satisfy the fresh read")
        monkeypatch.setattr(market, "_ws_api_json", should_not_run)
        result = await market.book_ticker("BTCUSDT", force_refresh=True)
        assert result["bid"] == 10.0
        assert result["source"] == "websocket_stream"

    asyncio.run(scenario())


def test_ws_api_reuses_one_connection_and_serializes_requests(monkeypatch):
    async def scenario():
        import json
        from app.data import market as market_module

        market = MarketData("https://example.invalid", "wss://example.invalid", ["BTCUSDT"])
        connect_calls = []

        class FakeSocket:
            closed = False

            def __init__(self):
                self.responses = []
                self.sent = []

            async def send(self, raw):
                request = json.loads(raw)
                self.sent.append(request)
                self.responses.append({"id": request["id"], "status": 200, "result": {"serverTime": 123}})

            async def recv(self):
                return json.dumps(self.responses.pop(0))

            async def close(self):
                self.closed = True

        socket = FakeSocket()

        async def connect(*args, **kwargs):
            connect_calls.append((args, kwargs))
            return socket

        monkeypatch.setattr(market_module.websockets, "connect", connect)
        first, second = await asyncio.gather(
            market._ws_api_json("/api/v3/time", {}),
            market._ws_api_json("/api/v3/time", {}),
        )
        assert first == {"serverTime": 123}
        assert second == {"serverTime": 123}
        assert len(connect_calls) == 1
        assert len(socket.sent) == 2
        assert socket.sent[0]["id"] != socket.sent[1]["id"]
        await market.stop()

    asyncio.run(scenario())


def test_ws_api_failover_uses_second_port(monkeypatch):
    async def scenario():
        import json
        from app.data import market as market_module

        market = MarketData(
            "https://example.invalid",
            "wss://example.invalid",
            ["BTCUSDT"],
            ws_api_url="wss://first.invalid/ws-api/v3,wss://second.invalid/ws-api/v3",
        )
        attempts = []

        class FakeSocket:
            closed = False

            async def send(self, raw):
                request = json.loads(raw)
                self.response = {"id": request["id"], "status": 200, "result": {"serverTime": 123}}

            async def recv(self):
                return json.dumps(self.response)

            async def close(self):
                self.closed = True

        async def connect(url, **kwargs):
            attempts.append(url)
            if "first" in url:
                raise OSError("first port unavailable")
            return FakeSocket()

        monkeypatch.setattr(market_module.websockets, "connect", connect)
        assert await market._ws_api_json("/api/v3/time", {}) == {"serverTime": 123}
        assert attempts == ["wss://first.invalid/ws-api/v3", "wss://second.invalid/ws-api/v3"]
        assert market.ws_api_url == "wss://second.invalid/ws-api/v3"
        await market.stop()

    asyncio.run(scenario())


def test_ws_api_url_normalization_removes_duplicate_scheme():
    assert MarketData._normalize_ws_api_url("wss://wss://ws-api.binance.com:443/ws-api/v3") == "wss://ws-api.binance.com:443/ws-api/v3"
    assert MarketData._normalize_ws_api_url("ws-api.binance.com:9443/ws-api/v3") == "wss://ws-api.binance.com:9443/ws-api/v3"
