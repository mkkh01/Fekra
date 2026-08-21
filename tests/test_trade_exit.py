import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import evaluate_trade_exit


def make_trade(direction: str) -> dict:
    return {
        "id": "test",
        "direction": direction,
        "entry": 100.0,
        "stop_loss": 95.0 if direction == "LONG" else 105.0,
        "take_profit_1": 110.0 if direction == "LONG" else 90.0,
    }


class TradeExitTests(unittest.TestCase):
    def test_long_closes_at_take_profit(self):
        result = evaluate_trade_exit(make_trade("LONG"), 110.0)
        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual(result["result"], "WIN")
        self.assertEqual(result["exit_reason"], "TAKE_PROFIT_1")
        self.assertEqual(result["exit_price"], 110.0)
        self.assertTrue(result["closed_at"])

    def test_short_closes_at_take_profit(self):
        result = evaluate_trade_exit(make_trade("SHORT"), 90.0)
        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual(result["result"], "WIN")
        self.assertEqual(result["exit_reason"], "TAKE_PROFIT_1")

    def test_long_stops_at_stop_loss(self):
        result = evaluate_trade_exit(make_trade("LONG"), 95.0)
        self.assertEqual(result["status"], "STOPPED")
        self.assertEqual(result["result"], "LOSS")
        self.assertEqual(result["exit_reason"], "STOP_LOSS")
        self.assertEqual(result["exit_price"], 95.0)
        self.assertTrue(result["closed_at"])

    def test_short_stops_at_stop_loss(self):
        result = evaluate_trade_exit(make_trade("SHORT"), 105.0)
        self.assertEqual(result["status"], "STOPPED")
        self.assertEqual(result["result"], "LOSS")
        self.assertEqual(result["exit_reason"], "STOP_LOSS")

    def test_trade_stays_active_between_levels(self):
        self.assertIsNone(evaluate_trade_exit(make_trade("LONG"), 102.0))
        self.assertIsNone(evaluate_trade_exit(make_trade("SHORT"), 98.0))


if __name__ == "__main__":
    unittest.main()
