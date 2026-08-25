import time
import unittest

from app.data.market import MarketData


class MarketQualityTests(unittest.TestCase):
    def test_closed_candle_age_is_based_on_exchange_time(self):
        market = MarketData("https://example.invalid", "wss://example.invalid", ["BTCUSDT"])
        now = time.time()
        interval = 900
        market.candles[("BTCUSDT", "15m")].append({"time": now - interval - 100, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "closed": True})
        market.last_closed_received_at[("BTCUSDT", "15m")] = now
        quality = market.data_quality_snapshot("BTCUSDT", "15m")
        self.assertEqual(quality["candle_age_seconds"], 100)
        self.assertTrue(quality["fresh"])
        self.assertEqual(quality["source"], None)

    def test_ticker_requires_recent_received_event_and_price(self):
        market = MarketData("https://example.invalid", "wss://example.invalid", ["BTCUSDT"])
        self.assertFalse(market.ticker_quality_snapshot("BTCUSDT")["fresh"])
        market.tickers["BTCUSDT"] = {"symbol": "BTCUSDT", "price": 100}
        market.last_ticker_at["BTCUSDT"] = time.time()
        self.assertTrue(market.ticker_quality_snapshot("BTCUSDT")["fresh"])
        market.last_ticker_at["BTCUSDT"] -= 46
        self.assertFalse(market.ticker_quality_snapshot("BTCUSDT")["fresh"])


if __name__ == "__main__":
    unittest.main()
