from __future__ import annotations

import asyncio
from pathlib import Path

from app.storage.store import Store
from app.strategies.ifvg.engine import IFVGConfig, STRATEGY_ID, STRATEGY_VERSION, analyze_ifvg


def candle(time: int, value: float = 100.0) -> dict:
    return {
        "time": time,
        "open_time": time,
        "close_time": time + 60,
        "open": value,
        "high": value + 1,
        "low": value - 1,
        "close": value,
        "volume": 100.0,
        "closed": True,
    }


def rows(interval_seconds: int, count: int = 40) -> list[dict]:
    return [{**candle(index * interval_seconds, 100 + (index % 3) * 0.1), "close_time": (index + 1) * interval_seconds} for index in range(count)]


def test_ifvg_requires_all_timeframes_and_reports_no_data():
    result = analyze_ifvg("BTCUSDT", {})
    assert result["strategy_id"] == STRATEGY_ID
    assert result["strategy_version"] == STRATEGY_VERSION
    assert result["decision"] == "REJECTED"
    assert result["primary_rejection_reason"] in {"NO_DATA", "DATA_GAP_FAIL"}


def test_ifvg_gap_inside_input_is_fail_closed():
    data = {interval: rows(seconds) for interval, seconds in (("4h", 14400), ("1h", 3600), ("15m", 900), ("5m", 300))}
    data["5m"].pop(12)
    result = analyze_ifvg("BTCUSDT", data)
    assert result["decision"] == "REJECTED"
    assert any("DATA_GAP" in gate for gate in result["failed_gates"])
    assert result["primary_rejection_reason"] == "DATA_GAP_FAIL"


def test_ifvg_config_rejects_unsafe_baseline_changes():
    config = IFVGConfig(max_valid_retests=2, minimum_net_rr=1.5)
    assert "INVALID_MAX_RETESTS" in config.validate()
    assert "MINIMUM_NET_RR_BELOW_BASELINE" in config.validate()


def test_ifvg_sqlite_isolated_round_trip(tmp_path: Path):
    store = Store(str(tmp_path / "ifvg.db"))

    async def scenario():
        setup = await store.create_ifvg_setup({
            "symbol": "BTCUSDT", "source_fvg_id": "fvg-1", "state": "IFVG_ACTIVE",
            "direction": "LONG", "zone_low": 99, "zone_high": 100,
            "config_version": "1.2.1-baseline", "failed_gates": [], "metadata": {},
        })
        assert setup["strategy_id"] == STRATEGY_ID
        listed = await store.list_ifvg_setups(symbol="BTCUSDT")
        assert listed[0]["id"] == setup["id"]
        trade = await store.create_ifvg_trade({
            "setup_id": setup["id"], "symbol": "BTCUSDT", "direction": "LONG",
            "state": "POSITION_OPEN", "entry_reference": 101, "entry_fill": 101,
            "stop_price": 98, "target_price": 108, "decision_time": "2026-01-01T00:00:00+00:00",
            "quantity": 1, "risk_per_unit_quote": 3, "metadata": {},
        })
        assert (await store.find_open_ifvg_trade("BTCUSDT"))["id"] == trade["id"]
        fill = await store.create_ifvg_fill({
            "trade_id": trade["id"], "fill_role": "ENTRY", "fill_sequence": 1,
            "reference_price": 101, "executable_price": 101, "quantity": 1,
            "fee_quote": 0.1, "event_time": "2026-01-01T00:00:00+00:00", "metadata": {},
        })
        assert (await store.list_ifvg_fills(trade["id"]))[0]["id"] == fill["id"]
        reservation = await store.create_ifvg_reservation({
            "reservation_key": "IFVG:BTCUSDT:setup-1", "symbol": "BTCUSDT", "reserved_quantity": 1,
        })
        assert reservation is not None
        assert await store.create_ifvg_reservation({
            "reservation_key": "IFVG:BTCUSDT:setup-1", "symbol": "BTCUSDT", "reserved_quantity": 1,
        }) is None

    asyncio.run(scenario())
