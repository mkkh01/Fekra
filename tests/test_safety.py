import time
import unittest

from app.analysis.safety import assess_entry, closed_candles, reprice_from_live_entry


class SafetyTests(unittest.TestCase):
    def test_unclosed_last_candle_is_excluded_by_server_time(self):
        now = 1_700_000_000
        rows = [
            {"time": now - 1_800, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1, "closed": True},
            {"time": now - 300, "open": 100, "high": 102, "low": 98, "close": 101, "volume": 1, "closed": True},
        ]
        result = closed_candles(rows, "15m", now=now)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["time"], now - 1_800)

    def test_unclosed_exchange_flag_is_excluded(self):
        now = time.time()
        rows = [{"time": now - 900, "closed": False}, {"time": now - 1_800, "closed": True}]
        self.assertEqual(len(closed_candles(rows, "15m", now=now)), 1)

    def test_live_entry_reprices_long_levels(self):
        signal = {"signal": "LONG", "entry": 100, "signal_price": 100, "stop_loss": 95, "rr": 2}
        levels = reprice_from_live_entry(signal, 102)
        self.assertEqual(levels["entry"], 102)
        self.assertEqual(levels["stop_loss"], 97)
        self.assertEqual(levels["take_profit_1"], 112)
        self.assertEqual(levels["take_profit_2"], 117)
        self.assertLess(levels["stop_loss"], levels["entry"])
        self.assertLess(levels["entry"], levels["take_profit_1"])

    def test_live_entry_reprices_short_levels(self):
        signal = {"signal": "SHORT", "entry": 100, "signal_price": 100, "stop_loss": 105, "rr": 2}
        levels = reprice_from_live_entry(signal, 98)
        self.assertEqual(levels["entry"], 98)
        self.assertEqual(levels["stop_loss"], 103)
        self.assertEqual(levels["take_profit_1"], 88)
        self.assertEqual(levels["take_profit_2"], 83)
        self.assertLess(levels["take_profit_2"], levels["take_profit_1"])
        self.assertLess(levels["take_profit_1"], levels["entry"])

    def test_entry_deviation_is_warning_not_global_block(self):
        signal = {
            "signal": "LONG", "entry": 100, "signal_price": 100, "stop_loss": 95,
            "rr": 2, "atr_percent": 5, "regime": "TRENDING_UP", "momentum": "CONFIRMED",
        }
        result = assess_entry(signal, 102, 2)
        self.assertIn("ENTRY_DEVIATION_ABOVE_SHADOW_LIMIT", result.warning_reasons)
        self.assertFalse(result.would_block)

    def test_reversal_risk_three_groups_is_shadow_block(self):
        signal = {
            "signal": "LONG", "entry": 100, "signal_price": 100, "stop_loss": 95,
            "rr": 2, "reversal_risk": 3,
            "reversal_risk_components": {"trend": 1, "structure": 1, "momentum": 1},
        }
        result = assess_entry(signal, 100, 2)
        self.assertTrue(result.would_block)
        self.assertIn("REVERSAL_RISK_SHADOW_BLOCK", result.blocked_reasons)


if __name__ == "__main__":
    unittest.main()
