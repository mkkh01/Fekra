"""
اختبارات التحويل التلقائي لمزود السوق البديل (Market Failover Engine).
"""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.market import VisionMarket


def test_provider_status_normal():
    VisionMarket._binance_cooldown_until = 0.0
    VisionMarket._active_provider = "binance"
    assert VisionMarket.provider_status() == "ok (Binance Vision)"


def test_provider_status_during_cooldown():
    VisionMarket._binance_cooldown_until = time.time() + 1200.0
    VisionMarket._active_provider = "mexc_backup"
    status = VisionMarket.provider_status()
    assert "ok" in status
    assert "MEXC" in status
    assert "تهدئة بينانس" in status


def test_automatic_failover_on_418():
    vm = VisionMarket()
    VisionMarket._binance_cooldown_until = 0.0
    VisionMarket._active_provider = "binance"

    # محاكاة رد 418 من بينانس ثم رد ناجح 200 من MEXC
    future_ban = int((time.time() + 300) * 1000)
    mock_binance_resp = MagicMock()
    mock_binance_resp.status_code = 418
    mock_binance_resp.text = f'{{"code": -1003, "msg": "Way too many requests; IP banned until {future_ban}."}}'

    mock_mexc_resp = MagicMock()
    mock_mexc_resp.status_code = 200
    mock_mexc_resp.json.return_value = {"symbol": "BTCUSDT", "price": "81500.00"}

    with patch.object(vm.sess, "get", side_effect=[mock_binance_resp, mock_mexc_resp]):
        px = vm.price("BTCUSDT")
        assert px == 81500.0
        assert VisionMarket._active_provider == "mexc_backup"
        assert VisionMarket._binance_cooldown_until > time.time()


def test_klines_column_normalization():
    vm = VisionMarket()
    # بيانات شمعة بصيغة MEXC (8 عناصر)
    mexc_kline_data = [
        [1700000000000, "100.0", "105.0", "99.0", "104.0", "1500.0", 1700000900000, "155000.0"]
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mexc_kline_data

    with patch.object(vm, "_get", return_value=mock_resp):
        df = vm.klines("BTCUSDT", "15m", 1)
        assert len(df) == 1
        assert list(df.columns) == ["open_time", "open", "high", "low", "close", "volume"]
        assert df.iloc[0]["close"] == 104.0
