from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.data.market import MarketData
from app.strategies.ifvg.service import IFVGService


def _exchange_item(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "status": "TRADING",
        "baseAsset": symbol[:-4],
        "quoteAsset": "USDT",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01", "minPrice": "0", "maxPrice": "0"},
            {"filterType": "LOT_SIZE", "stepSize": "0.0001", "minQty": "0.0001", "maxQty": "0"},
            {"filterType": "NOTIONAL", "minNotional": "5"},
        ],
    }


def test_exchange_filters_loads_complete_map_once():
    async def scenario():
        market = MarketData("https://example.invalid", "wss://example.invalid", ["BTCUSDT", "ETHUSDT"], analysis_intervals=["5m"])
        calls = []

        async def fake_get_json(path, params):
            calls.append((path, params))
            return {"symbols": [_exchange_item("BTCUSDT"), _exchange_item("ETHUSDT")]}

        market._get_json = fake_get_json
        btc = await market.exchange_filters("BTCUSDT")
        eth = await market.exchange_filters("ETHUSDT")
        assert btc["tick_size"] == 0.01
        assert eth["step_size"] == 0.0001
        assert calls == [("/api/v3/exchangeInfo", {})]

    asyncio.run(scenario())


def test_exchange_info_failure_is_cooled_down():
    async def scenario():
        market = MarketData("https://example.invalid", "wss://example.invalid", ["BTCUSDT"], analysis_intervals=["5m"])
        calls = 0

        async def failing_get_json(path, params):
            nonlocal calls
            calls += 1
            raise RuntimeError("418")

        market._get_json = failing_get_json
        with pytest.raises(RuntimeError, match="exchangeInfo unavailable"):
            await market.exchange_filters("BTCUSDT")
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            await market.exchange_filters("ETHUSDT")
        assert calls == 1

    asyncio.run(scenario())


def test_ifvg_cycle_aggregates_exchange_info_failure():
    async def scenario():
        settings = SimpleNamespace(ifvg_symbol_list=["BTCUSDT", "ETHUSDT"], ifvg_user_id=None, ifvg_fee_bps=10.0, ifvg_spread_bps=4.0, ifvg_entry_slippage_bps=2.0, ifvg_exit_slippage_bps=2.0, ifvg_stop_slippage_bps=4.0, ifvg_latency_bps=0.0)
        service = IFVGService(settings, SimpleNamespace(), SimpleNamespace())
        service.market.exchange_filters = lambda symbol: asyncio.sleep(0, result=_raise("418"))
        results = await service.run_once()
        assert len(results) == 2
        assert all(row["primary_rejection_reason"] == "EXCHANGE_INFO_UNAVAILABLE" for row in results)
        assert service.last_error.startswith("EXCHANGE_INFO_UNAVAILABLE")

    asyncio.run(scenario())


def _raise(message: str):
    raise RuntimeError(message)
