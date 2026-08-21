import asyncio
import tempfile
import unittest
from pathlib import Path

from app.notifications import PushNotifier
from app.storage.store import Store


class PushNotificationTests(unittest.TestCase):
    def test_sqlite_subscription_round_trip(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                store = Store(str(Path(directory) / "weeg.db"))
                payload = {
                    "endpoint": "https://push.example.test/subscription/12345678901234567890",
                    "p256dh": "p256dh-key-12345678901234567890",
                    "auth": "auth-key-1234567890",
                    "expiration_time": None,
                    "user_agent": "test-agent",
                }
                saved = await store.upsert_push_subscription(payload)
                rows = await store.list_push_subscriptions()
                self.assertEqual(saved["endpoint"], payload["endpoint"])
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["auth"], payload["auth"])
                self.assertTrue(await store.delete_push_subscription(payload["endpoint"]))
                self.assertEqual(await store.list_push_subscriptions(), [])

        asyncio.run(run())

    def test_unconfigured_notifier_is_safe(self):
        async def run():
            store = Store(":memory:")
            notifier = PushNotifier(store, None, None)
            result = await notifier.trade_opened({"id": "1", "symbol": "BTCUSDT", "direction": "LONG"})
            self.assertFalse(notifier.configured)
            self.assertEqual(result, {"sent": 0, "failed": 0, "removed": 0})

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
