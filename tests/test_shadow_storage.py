import asyncio
import tempfile
import unittest
from pathlib import Path

from app.storage.store import Store


class ShadowStorageTests(unittest.TestCase):
    def test_shadow_signal_round_trip_in_sqlite_fallback(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                store = Store(str(Path(directory) / "weeg.db"))
                row = await store.create_shadow_signal({
                    "id": "shadow-1",
                    "symbol": "BTCUSDT",
                    "timeframe": "15m",
                    "direction": "LONG",
                    "signal_candle_time": "2026-08-21T12:00:00+00:00",
                    "would_have_executed": False,
                    "would_block": True,
                    "blocked_reasons": ["REVERSAL_RISK_SHADOW_BLOCK"],
                    "warning_reasons": [],
                    "reversal_risk_components": {"trend": 1, "structure": 1, "momentum": 1},
                    "overextension_metrics": {},
                    "outcome_status": "PENDING",
                })
                self.assertEqual(row["id"], "shadow-1")
                rows = await store.list_shadow_signals()
                self.assertEqual(len(rows), 1)
                self.assertTrue(rows[0]["would_block"])
                self.assertEqual(rows[0]["blocked_reasons"], ["REVERSAL_RISK_SHADOW_BLOCK"])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
