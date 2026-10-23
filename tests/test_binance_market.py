"""
اختبارات تكامل السوق الحصري مع خوادم بينانس الرسمية (Binance-Only Market Engine).
"""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.market import VisionMarket


def test_provider_status_normal():
    VisionMarket._cooldown_until = 0.0
    VisionMarket._active_base = "https://data-api.binance.vision"
    assert "ok" in VisionMarket.provider_status()
    assert "Binance" in VisionMarket.provider_status()


def test_provider_status_during_cooldown():
    VisionMarket._cooldown_until = time.time() + 1200.0
    status = VisionMarket.provider_status()
    assert "cooldown" in status
    assert "تهدئة" in status


def test_binance_rotation_on_418():
    vm = VisionMarket()
    VisionMarket._cooldown_until = 0.0

    future_ban = int((time.time() + 300) * 1000)
    mock_ban_resp = MagicMock()
    mock_ban_resp.status_code = 418
    mock_ban_resp.text = f'{{"code": -1003, "msg": "Way too many requests; IP banned until {future_ban}."}}'

    mock_ok_resp = MagicMock()
    mock_ok_resp.status_code = 200
    mock_ok_resp.json.return_value = {"symbol": "BTCUSDT", "price": "81500.00"}

    with patch.object(vm.sess, "get", side_effect=[mock_ban_resp, mock_ok_resp]):
        px = vm.price("BTCUSDT")
        assert px == 81500.0
        assert VisionMarket._cooldown_until > time.time()


def test_binance_batch_pricing():
    vm = VisionMarket()
    VisionMarket._cooldown_until = 0.0

    mock_batch_resp = MagicMock()
    mock_batch_resp.status_code = 200
    mock_batch_resp.json.return_value = [
        {"symbol": "BTCUSDT", "price": "81500.00"},
        {"symbol": "ETHUSDT", "price": "2400.00"},
        {"symbol": "SOLUSDT", "price": "105.00"}
    ]

    with patch.object(vm.sess, "get", return_value=mock_batch_resp):
        prices = vm.prices(["BTCUSDT", "ETHUSDT"])
        assert len(prices) == 2
        assert prices["BTCUSDT"] == 81500.0
        assert prices["ETHUSDT"] == 2400.0


def test_binance_klines():
    vm = VisionMarket()
    VisionMarket._cooldown_until = 0.0

    sample_kline = [
        [1700000000000, "81000.0", "81600.0", "80900.0", "81500.0", "50.0", 1700000900000, "4000000.0", 100, "25.0", "2000000.0", "0"]
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = sample_kline

    with patch.object(vm, "_get", return_value=mock_resp):
        df = vm.klines("BTCUSDT", "15m", 1)
        assert len(df) == 1
        assert list(df.columns) == ["open_time", "open", "high", "low", "close", "volume"]
        assert df.iloc[0]["close"] == 81500.0
