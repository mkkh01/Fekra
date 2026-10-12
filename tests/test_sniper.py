import pytest
import pandas as pd
import numpy as np
from app import config, strategy

def make_dummy_klines(n=300, base_price=100.0, trend=0.0):
    """Generate synthetic OHLCV klines for testing."""
    times = pd.date_range("2026-01-01", periods=n, freq="15min")
    prices = [base_price]
    for i in range(1, n):
        prices.append(prices[-1] * (1.0 + trend + np.random.normal(0, 0.005)))
    
    rows = []
    for t, p in zip(times, prices):
        high = p * 1.004
        low = p * 0.996
        rows.append({
            "open_time": int(t.timestamp() * 1000),
            "open": p * 0.999,
            "high": high,
            "low": low,
            "close": p,
            "volume": 1000.0,
            "close_time": int(t.timestamp() * 1000) + 15 * 60 * 1000 - 1,
        })
    return pd.DataFrame(rows)


def test_sniper_engine_configuration():
    assert config.ENABLE_SNIPER is True
    assert config.ENABLE_DAY is False
    assert config.ENABLE_FALCON is False
    assert len(config.SNIPER_SYMBOLS) == 20
    assert "BTCUSDT" in config.SNIPER_SYMBOLS
    assert "ETHUSDT" in config.SNIPER_SYMBOLS
    assert "SOLUSDT" in config.SNIPER_SYMBOLS
    assert config.SNIPER["tp_min_pct"] == 0.0035
    assert config.SNIPER["tp_max_pct"] == 0.0075
    assert config.SNIPER["sl_pct"] == 0.030


def test_sniper_scan_debug_insufficient_bars():
    df = make_dummy_klines(n=50)
    sig, entry, sl, tp, debug = strategy.sniper_scan_debug("BTCUSDT", df)
    assert sig == 0
    assert entry is None
    assert debug.get("reject") == "warmup"


def test_sniper_scan_debug_normal_market():
    # In a neutral random walk, returns 5-tuple
    df = make_dummy_klines(n=250)
    sig, entry, sl, tp, debug = strategy.sniper_scan_debug("BTCUSDT", df)
    assert sig in (-1, 0, 1)
    assert isinstance(debug, dict)


def test_sniper_scan_debug_tp_sl_rules():
    # If a signal is produced, TP must be between 0.35% and 0.75%
    # Test directly by passing mock indicators into sniper_signal
    # Construct an extreme oversold dataframe
    df = make_dummy_klines(n=260, base_price=100.0)
    # Give a steady baseline so EMA200 is established
    # Then crash the last 10 bars
    for i in range(240, 258):
        df.loc[i, "close"] = df.loc[i-1, "close"] * 0.98
        df.loc[i, "open"] = df.loc[i-1, "close"]
        df.loc[i, "high"] = df.loc[i, "open"]
        df.loc[i, "low"] = df.loc[i, "close"]

    # Bar 258 (penultimate completed bar)
    df.loc[258, "open"] = df.loc[257, "close"] * 0.98
    df.loc[258, "low"] = df.loc[258, "open"] * 0.95
    df.loc[258, "close"] = df.loc[258, "open"] * 1.01
    df.loc[258, "high"] = df.loc[258, "close"] * 1.002

    sig, entry, sl, tp, debug = strategy.sniper_scan_debug("BTCUSDT", df)
    if sig != 0:
        if sig == 1:
            tp_pct = (tp - entry) / entry
            assert 0.0035 <= tp_pct <= 0.0075
            assert sl < entry
        elif sig == -1:
            tp_pct = (entry - tp) / entry
            assert 0.0035 <= tp_pct <= 0.0075
            assert sl > entry
