import asyncio
import time
import unittest
from unittest.mock import AsyncMock

from app.data.market import MarketData


class MarketHealthTests(unittest.TestCase):
    def test_health_snapshot_reports_live_feed_only_after_recent_event(self):
        async def run():
            market = MarketData("https://example.invalid", "wss://example.invalid", ["BTCUSDT"], "15m", ["15m"])
            market._task = asyncio.current_task()
            market.last_event_at = time.time()
            self.assertTrue(market.health_snapshot()["live_feed"])
            market.last_event_at -= 60
            self.assertFalse(market.health_snapshot()["live_feed"])

        asyncio.run(run())

    def test_stale_candle_cache_is_reloaded(self):
        async def run():
            market = MarketData("https://example.invalid", "wss://example.invalid", ["BTCUSDT"], "15m", ["15m"])
            market.candles[("BTCUSDT", "15m")].extend([{"time": index} for index in range(30)])
            market.last_candle_at[("BTCUSDT", "15m")] = time.time() - 120
            market.load_history = AsyncMock(return_value=[{"time": 999}])
            rows = await market.ensure_history("BTCUSDT", "15m")
            market.load_history.assert_awaited_once_with("BTCUSDT", "15m")
            self.assertEqual(rows, [{"time": 999}])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
