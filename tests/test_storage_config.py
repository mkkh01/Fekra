import os
import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.storage.store import Store


class StorageConfigTests(unittest.TestCase):
    def test_postgres_is_preferred_over_rest(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as db:
            store = Store(
                db.name,
                supabase_url="https://example.supabase.co",
                supabase_key="invalid",
                database_url="postgresql://example.invalid/postgres",
            )
            self.assertTrue(store.postgres_configured)
            self.assertEqual(store.backend_name, "postgres_unavailable")

    def test_sqlite_is_not_marked_persistent(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as db:
            store = Store(db.name)
            self.assertFalse(store.persistent_storage_configured)
            self.assertFalse(store.has_persistent_storage)
            self.assertEqual(store.backend_name, "sqlite_ephemeral")

    def test_postgres_url_can_be_read_from_supabase_url_legacy_field(self):
        from app.config import Settings
        settings = Settings(supabase_url="postgresql://postgres.example/postgres")
        self.assertEqual(settings.postgres_dsn, "postgresql://postgres.example/postgres")
        self.assertTrue(settings.supabase_http_url.startswith("https://"))

    def test_closed_and_stopped_trades_include_exit_price(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as db:
            store = Store(db.name)
            with sqlite3.connect(db.name) as connection:
                for trade_id, status, direction, entry, pnl in [
                    ("win", "CLOSED", "LONG", 100.0, 10.0),
                    ("loss", "STOPPED", "SHORT", 100.0, -5.0),
                ]:
                    payload = {"id": trade_id, "symbol": "BTCUSDT", "direction": direction, "entry": entry, "pnl": pnl, "status": status, "closed_at": "2026-08-19T00:00:00+00:00"}
                    connection.execute("insert into trades(id,payload,status,created_at) values(?,?,?,?)", (trade_id, json.dumps(payload), status, payload["closed_at"]))
                connection.commit()
            rows = asyncio.run(store.list_trades("CLOSED_OR_STOPPED"))
            by_id = {row["id"]: row for row in rows}
            self.assertEqual(by_id["win"]["exit_price"], 110.0)
            self.assertEqual(by_id["loss"]["exit_price"], 105.0)
            self.assertTrue(by_id["loss"]["closed_at"])


if __name__ == "__main__":
    unittest.main()
