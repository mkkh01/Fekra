import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import _analyze_mtf


def result(signal: str, bias: str, ready: bool | None = None) -> dict:
    return {
        "signal": signal,
        "bias": bias,
        "confidence": 80,
        "structure": bias,
        "regime": "TRENDING_UP" if bias == "LONG" else "TRENDING_DOWN" if bias == "SHORT" else "RANGING",
        "ready": signal in ("LONG", "SHORT") if ready is None else ready,
        "reasons": [],
        "entry": 100.0,
        "stop_loss": 98.0,
        "take_profit_1": 104.0,
        "take_profit_2": 106.0,
        "rr": 2.0,
        "updated_at": "2026-08-19T00:00:00+00:00",
    }


class MtfTests(unittest.TestCase):
    def test_15m_long_is_rejected_against_4h_short(self):
        async def run():
            with patch("main.market.ensure_history", new=AsyncMock(return_value=[])), patch(
                "main.analyze",
                side_effect=lambda symbol, rows, interval, threshold, minimum_rr: {
                    "4h": result("SHORT", "SHORT"),
                    "1h": result("LONG", "LONG"),
                    "15m": result("LONG", "LONG"),
                }[interval],
            ):
                output = await _analyze_mtf("BTCUSDT")
                self.assertEqual(output["signal"], "NO TRADE")
                self.assertFalse(output["ready"])
                self.assertEqual(output["mtf_alignment"], "VETO")
                self.assertTrue(any("4h" in veto for veto in output["mtf_vetoes"]))

        asyncio.run(run())

    def test_trade_requires_all_three_timeframes_same_direction(self):
        async def run():
            with patch("main.market.ensure_history", new=AsyncMock(return_value=[])), patch(
                "main.analyze",
                side_effect=lambda symbol, rows, interval, threshold, minimum_rr: {
                    "4h": result("LONG", "LONG"),
                    "1h": result("NO TRADE", "LONG", ready=False),
                    "15m": result("LONG", "LONG"),
                }[interval],
            ):
                output = await _analyze_mtf("BTCUSDT")
                self.assertEqual(output["signal"], "NO TRADE")
                self.assertFalse(output["ready"])
                self.assertEqual(output["mtf_alignment"], "VETO")
                self.assertTrue(any("1h" in veto for veto in output["mtf_vetoes"]))

        asyncio.run(run())

    def test_15m_long_is_accepted_only_when_all_timeframes_align_and_are_ready(self):
        async def run():
            with patch("main.market.ensure_history", new=AsyncMock(return_value=[])), patch(
                "main.analyze",
                side_effect=lambda symbol, rows, interval, threshold, minimum_rr: result("LONG", "LONG", ready=True),
            ):
                output = await _analyze_mtf("BTCUSDT")
                self.assertEqual(output["signal"], "LONG")
                self.assertTrue(output["ready"])
                self.assertEqual(output["mtf_alignment"], "ALIGNED")
                self.assertEqual(output["mtf_vetoes"], [])
                self.assertEqual(set(output["timeframes"]), {"4h", "1h", "15m"})

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
