from __future__ import annotations

import asyncio

from app.data import market as market_module
from app.data.market import MarketData


class _Response:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("GET", "https://example.invalid")
            raise httpx.HTTPStatusError(
                "status", request=request, response=httpx.Response(self.status_code, request=request)
            )

    def json(self):
        return self._payload


class _Client:
    def __init__(self, calls, responses):
        self.calls = calls
        self.responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params):
        self.calls.append(url)
        return self.responses.pop(0)


def test_market_rest_failover_uses_second_official_endpoint(monkeypatch):
    async def scenario():
        market = MarketData("https://data-api.binance.vision", "wss://example.invalid", ["BTCUSDT"])
        market.rest_urls = ["https://api.binance.com", "https://data-api.binance.vision"]

        async def ws_unavailable(path, params):
            raise RuntimeError("offline test")

        monkeypatch.setattr(market, "_ws_api_json", ws_unavailable)
        calls = []
        responses = [_Response(418), _Response(200, {"ok": True})]
        monkeypatch.setattr(market_module.httpx, "AsyncClient", lambda **kwargs: _Client(calls, responses))
        payload = await market._get_json("/api/v3/time", {})
        assert payload == {"ok": True}
        assert calls == [
            "https://api.binance.com/api/v3/time",
            "https://data-api.binance.vision/api/v3/time",
        ]
        assert market.rest_health()["available"] is True

    asyncio.run(scenario())


def test_market_rest_circuit_breaker_stops_after_all_endpoints_fail(monkeypatch):
    async def scenario():
        market = MarketData("https://data-api.binance.vision", "wss://example.invalid", ["BTCUSDT"])
        market.rest_urls = ["https://api.binance.com", "https://data-api.binance.vision"]

        async def ws_unavailable(path, params):
            raise RuntimeError("offline test")

        monkeypatch.setattr(market, "_ws_api_json", ws_unavailable)
        calls = []
        responses = [_Response(418), _Response(418)]
        monkeypatch.setattr(market_module.httpx, "AsyncClient", lambda **kwargs: _Client(calls, responses))
        try:
            await market._get_json("/api/v3/time", {})
        except RuntimeError as exc:
            assert "BINANCE_REST_RATE_LIMITED" in str(exc)
        else:
            raise AssertionError("expected circuit breaker error")
        assert len(calls) == 2
        assert market.rest_health()["available"] is False

    asyncio.run(scenario())


def test_market_ws_api_is_primary(monkeypatch):
    async def scenario():
        market = MarketData("https://example.invalid", "wss://example.invalid", ["BTCUSDT"])
        calls = []

        async def ws_response(path, params):
            calls.append((path, params))
            return {"serverTime": 123}

        monkeypatch.setattr(market, "_ws_api_json", ws_response)
        payload = await market._get_json("/api/v3/time", {})
        assert payload == {"serverTime": 123}
        assert calls == [("/api/v3/time", {})]
        assert market._last_transport_source == "websocket_api"

    asyncio.run(scenario())
