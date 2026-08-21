import time
import unittest
from unittest.mock import patch

from app.analysis import engine
from app.analysis.profiles import PROFILES


class EngineClosedCandleTests(unittest.TestCase):
    def test_analyze_ignores_forming_last_candle(self):
        now = time.time()
        candles = []
        for index in range(80):
            candle_time = now - (80 - index) * 900
            close = 100.0 + index * 0.2
            candles.append({
                "time": candle_time,
                "open": close - 0.05,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1000.0,
                "closed": True,
            })
        forming = {**candles[-1], "time": now - 30, "closed": False, "close": 999.0}
        candles[-1] = forming
        with patch.object(engine, "profile_for", return_value=PROFILES["major"]):
            result = engine.analyze("BTCUSDT", candles, "15m", threshold=65, minimum_rr=2.0)
        self.assertNotEqual(result.get("signal_price"), 999.0)
        self.assertTrue(result.get("signal_candle_time"))
        self.assertLess(float(result["signal_age_seconds"]), 90000)


if __name__ == "__main__":
    unittest.main()
