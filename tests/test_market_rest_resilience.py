import pytest

from app.market.service import MarketService
from app.state import RuntimeState


@pytest.mark.asyncio
async def test_historical_context_is_serialized_and_cached(monkeypatch) -> None:
    service = MarketService(RuntimeState())
    calls = []

    async def fake_candles(symbol: str, interval: str, limit: int):
        calls.append(interval)
        return [{"open_time": 1, "close_time": 2, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10}]

    monkeypatch.setattr(service, "candles", fake_candles)
    first = await service.historical_context("BTCUSDT")
    second = await service.historical_context("BTCUSDT")

    assert first["ready"] is True
    assert set(calls) == {"5m", "15m", "1h", "4h", "1d"}
    assert len(calls) == 5
    assert second["ready"] is True
    assert len(calls) == 5


def test_binance_rest_failover_hosts_include_data_only_endpoint() -> None:
    assert "https://api.binance.com" in MarketService.rest_hosts
    assert "https://api1.binance.com" in MarketService.rest_hosts
    assert "https://api2.binance.com" in MarketService.rest_hosts
    assert "https://api3.binance.com" in MarketService.rest_hosts
    assert "https://data-api.binance.vision" in MarketService.rest_hosts
    assert 418 in MarketService.retryable_statuses
    assert 429 in MarketService.retryable_statuses


@pytest.mark.asyncio
async def test_binance_418_fails_over_to_next_host(monkeypatch) -> None:
    import app.market.service as market_module

    service = MarketService(RuntimeState())
    service._min_rest_interval = 0
    requested_hosts = []

    class FakeResponse:
        def __init__(self, status_code: int, payload):
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def json(self):
            return self._payload

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None):
            requested_hosts.append(url.split("/")[2])
            if len(requested_hosts) == 1:
                return FakeResponse(418, {"code": -1003})
            return FakeResponse(200, {"ok": True})

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(market_module.httpx, "AsyncClient", lambda **kwargs: FakeClient())
    monkeypatch.setattr(market_module.asyncio, "sleep", no_sleep)
    payload = await service._get_binance_json("/api/v3/time", {})

    assert payload == {"ok": True}
    assert requested_hosts[:2] == ["api.binance.com", "api1.binance.com"]
