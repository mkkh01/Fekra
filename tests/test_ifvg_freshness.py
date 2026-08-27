from __future__ import annotations

import time

from app.strategies.ifvg.engine import analyze_ifvg


def _rows(seconds: int, count: int = 40):
    return [
        {
            "time": index * seconds,
            "open_time": index * seconds,
            "close_time": (index + 1) * seconds,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100.0,
            "closed": True,
        }
        for index in range(count)
    ]


def test_live_ifvg_rejects_any_stale_decision_input():
    now = time.time()
    rows = {interval: _rows(seconds) for interval, seconds in (("4h", 14400), ("1h", 3600), ("15m", 900), ("5m", 300))}
    market = {
        "live_decision": True,
        "observed_at": now - 120,
        "book_ticker": {"ask": 100.0, "observed_at": now - 10},
        "clock": {"valid": True, "clock_skew_ok": True},
        "decision_freshness": {interval: {"fresh": interval != "1h"} for interval in rows},
    }
    result = analyze_ifvg("BTCUSDT", rows, market=market, portfolio={"clock_skew_ms": 0})
    assert result["decision"] == "REJECTED"
    assert "STALE_DECISION_DATA_1h" in result["failed_gates"]
    assert "STALE_EXCHANGE_INFO" in result["failed_gates"]
    assert "STALE_BOOK_TICKER" in result["failed_gates"]


def test_historical_ifvg_analysis_does_not_require_live_market_metadata():
    rows = {interval: _rows(seconds) for interval, seconds in (("4h", 14400), ("1h", 3600), ("15m", 900), ("5m", 300))}
    result = analyze_ifvg("BTCUSDT", rows, market={}, portfolio={})
    assert "STALE_EXCHANGE_INFO" not in result["failed_gates"]
    assert "STALE_BOOK_TICKER" not in result["failed_gates"]
