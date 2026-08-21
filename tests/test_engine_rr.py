import unittest
from unittest.mock import patch

from app.analysis import engine
from app.analysis.profiles import PROFILES


class EngineRiskRewardTests(unittest.TestCase):
    def test_target_rr_uses_profile_minimum(self):
        candles = []
        for index in range(80):
            close = 100.0 + index * 0.2
            candles.append({
                "time": index * 900,
                "open": close - 0.05,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1000.0,
            })
        with patch.object(engine, "profile_for", return_value=PROFILES["volatile"]):
            result = engine.analyze("TESTUSDT", candles, "15m", threshold=65, minimum_rr=2.0)
        self.assertEqual(result["applied_minimum_rr"], 2.2)
        self.assertEqual(result["rr"], 2.2)
        self.assertGreater(result["take_profit_1"], result["entry"])

    def test_major_profile_keeps_two_to_one_rr(self):
        candles = []
        for index in range(80):
            close = 100.0 + index * 0.2
            candles.append({
                "time": index * 900,
                "open": close - 0.05,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1000.0,
            })
        with patch.object(engine, "profile_for", return_value=PROFILES["major"]):
            result = engine.analyze("BTCUSDT", candles, "15m", threshold=65, minimum_rr=2.0)
        self.assertEqual(result["applied_minimum_rr"], 2.0)
        self.assertEqual(result["rr"], 2.0)


if __name__ == "__main__":
    unittest.main()
