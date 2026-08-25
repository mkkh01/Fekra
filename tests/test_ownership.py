import asyncio
import tempfile
import unittest
from pathlib import Path

from app.storage.store import Store


class OwnershipTests(unittest.TestCase):
    def test_trade_visibility_and_update_ownership(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as db:
            store = Store(db.name)
            asyncio.run(store.create_trade({"id": "auto", "symbol": "BTCUSDT", "direction": "LONG", "entry": 100, "stop_loss": 99, "take_profit_1": 102, "take_profit_2": 104, "status": "OPEN", "auto_created": True}))
            asyncio.run(store.create_trade({"id": "alice", "symbol": "ETHUSDT", "direction": "LONG", "entry": 100, "stop_loss": 99, "take_profit_1": 102, "take_profit_2": 104, "status": "OPEN", "auto_created": False, "user_id": "alice"}))
            asyncio.run(store.create_trade({"id": "bob", "symbol": "SOLUSDT", "direction": "LONG", "entry": 100, "stop_loss": 99, "take_profit_1": 102, "take_profit_2": 104, "status": "OPEN", "auto_created": False, "user_id": "bob"}))
            visible = asyncio.run(store.list_trades("OPEN", user_id="alice"))
            self.assertEqual({row["id"] for row in visible}, {"auto", "alice"})
            self.assertIsNone(asyncio.run(store.update_trade("bob", {"status": "CLOSED"}, user_id="alice")))
            self.assertIsNotNone(asyncio.run(store.update_trade("alice", {"status": "CLOSED"}, user_id="alice")))

    def test_push_and_settings_are_user_scoped(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as db:
            store = Store(db.name)
            subscription = {"endpoint": "https://push.example/alice", "p256dh": "p", "auth": "a"}
            asyncio.run(store.upsert_push_subscription(subscription, user_id="alice"))
            self.assertEqual(len(asyncio.run(store.list_push_subscriptions(user_id="alice"))), 1)
            self.assertEqual(len(asyncio.run(store.list_push_subscriptions(user_id="bob"))), 0)
            asyncio.run(store.save_settings({"minimum_rr": 2.5}, user_id="alice"))
            self.assertEqual(asyncio.run(store.get_settings(user_id="alice"))["minimum_rr"], 2.5)
            self.assertEqual(asyncio.run(store.get_settings(user_id="bob")), {})
            self.assertTrue(asyncio.run(store.delete_push_subscription(subscription["endpoint"], user_id="alice")))
            self.assertEqual(len(asyncio.run(store.list_push_subscriptions(user_id="alice"))), 0)


if __name__ == "__main__":
    unittest.main()
