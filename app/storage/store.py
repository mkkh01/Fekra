import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

import httpx


class Store:
    PG_TRADE_FIELDS = {
        "id", "user_id", "symbol", "direction", "timeframe", "signal_time", "entry",
        "stop_loss", "take_profit_1", "take_profit_2", "risk_reward", "confidence", "regime",
        "structure_state", "liquidity_state", "fvg_state", "volume_state", "momentum_state",
        "status", "result", "pnl", "max_favorable_excursion", "max_adverse_excursion",
        "exit_reason", "exit_price", "created_at", "closed_at", "source", "auto_created", "asset_profile",
        "signal_reasons", "mtf_alignment", "mtf_vetoes", "mtf_timeframes",
    }
    PG_UPDATE_FIELDS = PG_TRADE_FIELDS - {"id", "created_at"}

    def __init__(
        self,
        db_path: str,
        supabase_url: str | None = None,
        supabase_key: str | list[str] | None = None,
        redis_url: str | None = None,
        database_url: str | None = None,
    ):
        self.db_path = db_path
        self.supabase_url = supabase_url
        self.database_url = database_url
        self.redis_url = redis_url
        self.supabase_keys = [supabase_key] if isinstance(supabase_key, str) and supabase_key else list(supabase_key or [])
        self.supabase_key = self.supabase_keys[0] if self.supabase_keys else None
        self._init_sqlite()
        self.persistent_storage_ready = False
        self.storage_last_error: str | None = None
        self.storage_last_check_at: str | None = None
        self.storage_key_source: str | None = None
        self.redis = None
        if redis_url:
            try:
                import redis.asyncio as redis
                self.redis = redis.from_url(redis_url, decode_responses=True)
            except Exception:
                self.redis = None

    @property
    def persistent_storage_configured(self) -> bool:
        return bool(self.database_url or (self.supabase_url and self.supabase_keys))

    @property
    def postgres_configured(self) -> bool:
        return bool(self.database_url)

    @property
    def supabase_key_count(self) -> int:
        return len(self.supabase_keys)

    @property
    def has_persistent_storage(self) -> bool:
        return self.persistent_storage_ready

    @property
    def backend_name(self) -> str:
        if self.persistent_storage_ready:
            return "postgres" if self.storage_key_source == "postgres" else "supabase"
        if self.database_url:
            return "postgres_unavailable"
        if self.supabase_url and self.supabase_keys:
            return "supabase_unavailable"
        return "sqlite_ephemeral"

    def _init_sqlite(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("create table if not exists trades (id text primary key, payload text not null, status text not null, created_at text not null)")
            db.execute("create table if not exists settings (id integer primary key check(id=1), payload text not null)")
            db.execute("create table if not exists push_subscriptions (endpoint text primary key, p256dh text not null, auth text not null, expiration_time real, user_agent text, created_at text not null, updated_at text not null)")
            db.commit()

    async def _pg_query(self, query: str, params: tuple | list = (), fetch: str = "all"):
        if not self.database_url:
            return None
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("psycopg غير مثبت") from exc

        def run_query():
            dsn = self.database_url
            if "sslmode=" not in dsn:
                dsn += "&sslmode=require" if "?" in dsn else "?sslmode=require"
            with psycopg.connect(
                dsn,
                row_factory=dict_row,
                connect_timeout=8,
                options="-c statement_timeout=12000",
                application_name="weeg-trading-dashboard",
            ) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(query, params)
                    if fetch == "one":
                        result = cursor.fetchone()
                    elif fetch == "none":
                        result = None
                    else:
                        result = cursor.fetchall()
                conn.commit()
                return result

        try:
            return await asyncio.wait_for(asyncio.to_thread(run_query), timeout=20)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("PostgreSQL query timeout") from exc

    async def _supabase(self, table: str, method: str = "GET", params: dict | None = None, data: Any = None):
        if not (self.supabase_url and self.supabase_key):
            return None
        headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.request(
                method,
                f"{self.supabase_url.rstrip('/')}/rest/v1/{table}",
                headers=headers,
                params=params,
                json=data,
            )
            response.raise_for_status()
            return response.json()

    async def check_persistent_storage(self) -> bool:
        self.storage_last_check_at = datetime.now(timezone.utc).isoformat()
        errors = []

        if self.database_url:
            try:
                await self._pg_query("select 1 as ok", fetch="one")
                self.persistent_storage_ready = True
                self.storage_last_error = None
                self.storage_key_source = "postgres"
                return True
            except Exception as exc:
                errors.append(f"postgres_{type(exc).__name__}")

        for index, key in enumerate(self.supabase_keys):
            self.supabase_key = key
            try:
                await self._supabase("weeg_trades", params={"select": "id", "limit": "1"})
                self.persistent_storage_ready = True
                self.storage_last_error = None
                self.storage_key_source = f"candidate_{index + 1}"
                return True
            except httpx.HTTPStatusError as exc:
                errors.append(f"supabase_http_{exc.response.status_code}")
            except Exception as exc:
                errors.append(f"supabase_{type(exc).__name__}")

        self.persistent_storage_ready = False
        self.storage_key_source = None
        self.storage_last_error = ";".join(errors) or "persistent_storage_not_configured"
        return False

    async def list_trades(self, status: str | None = None) -> list[dict[str, Any]]:
        if self.database_url:
            query = "select * from public.weeg_trades"
            params: list[Any] = []
            if status == "CLOSED_OR_STOPPED":
                query += " where status in ('CLOSED','STOPPED')"
            elif status:
                query += " where status = %s"
                params.append(status)
            query += " order by signal_time desc limit 200"
            try:
                return self._decorate_trades(await self._pg_query(query, params))
            except Exception:
                if self.persistent_storage_ready and self.storage_key_source == "postgres":
                    raise
        try:
            params = {"select": "*", "order": "signal_time.desc", "limit": "200"}
            if status == "CLOSED_OR_STOPPED":
                params["status"] = "in.(CLOSED,STOPPED)"
            elif status:
                params["status"] = f"eq.{status}"
            remote = await self._supabase("weeg_trades", params=params)
            if remote is not None:
                return self._decorate_trades(remote)
        except Exception:
            pass
        with sqlite3.connect(self.db_path) as db:
            query = "select payload from trades"
            args = []
            if status == "CLOSED_OR_STOPPED":
                query += " where status in ('CLOSED','STOPPED')"
            elif status:
                query += " where status=?"
                args.append(status)
            query += " order by created_at desc limit 200"
            return self._decorate_trades([json.loads(row[0]) for row in db.execute(query, args).fetchall()])

    async def list_active_trades(self) -> list[dict[str, Any]]:
        if self.database_url:
            query = "select * from public.weeg_trades where status in ('PENDING','OPEN','PARTIAL') order by created_at asc limit 500"
            try:
                return await self._pg_query(query)
            except Exception:
                if self.persistent_storage_ready and self.storage_key_source == "postgres":
                    raise
        try:
            remote = await self._supabase("weeg_trades", params={
                "select": "*",
                "status": "in.(PENDING,OPEN,PARTIAL)",
                "order": "created_at.asc",
                "limit": "500",
            })
            if remote is not None:
                return remote
        except Exception:
            pass
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("select payload from trades where status in ('PENDING','OPEN','PARTIAL') order by created_at asc limit 500").fetchall()
            return [json.loads(row[0]) for row in rows]

    async def upsert_push_subscription(self, subscription: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "endpoint": str(subscription["endpoint"]),
            "p256dh": str(subscription["p256dh"]),
            "auth": str(subscription["auth"]),
            "expiration_time": subscription.get("expiration_time"),
            "user_agent": subscription.get("user_agent"),
            "updated_at": now,
            "created_at": now,
        }
        if self.database_url:
            query = """
                insert into public.weeg_push_subscriptions
                    (endpoint, p256dh, auth, expiration_time, user_agent, created_at, updated_at)
                values (%s, %s, %s, %s, %s, %s, %s)
                on conflict (endpoint) do update set
                    p256dh = excluded.p256dh,
                    auth = excluded.auth,
                    expiration_time = excluded.expiration_time,
                    user_agent = excluded.user_agent,
                    updated_at = excluded.updated_at
                returning *
            """
            return await self._pg_query(query, tuple(data.values()), fetch="one")
        if self.persistent_storage_configured:
            remote = await self._supabase("weeg_push_subscriptions", method="POST", params={"on_conflict": "endpoint"}, data=data)
            if remote:
                return remote[0]
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "insert or replace into push_subscriptions(endpoint,p256dh,auth,expiration_time,user_agent,created_at,updated_at) values(?,?,?,?,?,?,?)",
                tuple(data.values()),
            )
            db.commit()
        return data

    async def list_push_subscriptions(self) -> list[dict[str, Any]]:
        if self.database_url:
            return await self._pg_query("select endpoint, p256dh, auth, expiration_time, user_agent from public.weeg_push_subscriptions order by updated_at desc limit 100")
        if self.persistent_storage_configured:
            try:
                remote = await self._supabase("weeg_push_subscriptions", params={"select": "endpoint,p256dh,auth,expiration_time,user_agent", "order": "updated_at.desc", "limit": "100"})
                if remote is not None:
                    return remote
            except Exception:
                pass
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("select endpoint,p256dh,auth,expiration_time,user_agent from push_subscriptions order by updated_at desc limit 100").fetchall()
            return [dict(zip(("endpoint", "p256dh", "auth", "expiration_time", "user_agent"), row)) for row in rows]

    async def delete_push_subscription(self, endpoint: str) -> bool:
        if self.database_url:
            row = await self._pg_query("delete from public.weeg_push_subscriptions where endpoint = %s returning endpoint", (endpoint,), fetch="one")
            return bool(row)
        if self.persistent_storage_configured:
            try:
                await self._supabase("weeg_push_subscriptions", method="DELETE", params={"endpoint": f"eq.{endpoint}"})
                return True
            except Exception:
                pass
        with sqlite3.connect(self.db_path) as db:
            cursor = db.execute("delete from push_subscriptions where endpoint=?", (endpoint,))
            db.commit()
            return cursor.rowcount > 0

    async def find_open_auto_trade(self, symbol: str, timeframe: str) -> dict[str, Any] | None:
        if self.database_url:
            query = """
                select * from public.weeg_trades
                where symbol = %s and timeframe = %s and auto_created = true
                  and status in ('PENDING','OPEN','PARTIAL')
                order by signal_time desc limit 1
            """
            try:
                return await self._pg_query(query, (symbol.upper(), timeframe), fetch="one")
            except Exception:
                if self.persistent_storage_ready and self.storage_key_source == "postgres":
                    raise
        try:
            remote = await self._supabase("weeg_trades", params={
                "select": "*", "symbol": f"eq.{symbol.upper()}", "timeframe": f"eq.{timeframe}",
                "auto_created": "eq.true", "status": "in.(PENDING,OPEN,PARTIAL)",
                "order": "signal_time.desc", "limit": "1",
            })
            if remote:
                return remote[0]
        except Exception:
            pass
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "select payload from trades where json_extract(payload, '$.symbol')=? and json_extract(payload, '$.timeframe')=? and json_extract(payload, '$.auto_created')=1 and status in ('PENDING','OPEN','PARTIAL') order by created_at desc limit 1",
                (symbol.upper(), timeframe),
            ).fetchone()
            return json.loads(row[0]) if row else None

    @staticmethod
    def _decorate_trade(trade: dict[str, Any]) -> dict[str, Any]:
        if trade.get("exit_price") is None and trade.get("status") in ("CLOSED", "STOPPED") and trade.get("pnl") is not None:
            try:
                entry = float(trade["entry"])
                pnl_percent = float(trade["pnl"])
                exit_price = entry * (1 + pnl_percent / 100) if trade.get("direction") == "LONG" else entry * (1 - pnl_percent / 100)
                trade = {**trade, "exit_price": round(exit_price, 8)}
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                pass
        return trade

    @classmethod
    def _decorate_trades(cls, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [cls._decorate_trade(trade) for trade in trades]

    @staticmethod
    def _pg_value(field: str, value: Any) -> Any:
        if field in {"signal_reasons", "mtf_vetoes", "mtf_timeframes"}:
            try:
                from psycopg.types.json import Jsonb
                return Jsonb(value if value is not None else [])
            except ImportError:
                return json.dumps(value if value is not None else [])
        return value

    async def create_trade(self, trade: dict[str, Any]) -> dict[str, Any]:
        trade = {**trade, "created_at": datetime.now(timezone.utc).isoformat()}
        if self.database_url:
            data = {key: value for key, value in trade.items() if key in self.PG_TRADE_FIELDS}
            fields = list(data)
            placeholders = ", ".join(["%s"] * len(fields))
            values = [self._pg_value(field, data[field]) for field in fields]
            query = f"insert into public.weeg_trades ({', '.join(fields)}) values ({placeholders}) returning *"
            return await self._pg_query(query, values, fetch="one")
        if self.persistent_storage_configured:
            remote = await self._supabase("weeg_trades", method="POST", data=trade)
            if not remote:
                raise RuntimeError("Supabase لم يُرجع الصفقة بعد الحفظ")
            return remote[0]
        with sqlite3.connect(self.db_path) as db:
            db.execute("insert or replace into trades(id,payload,status,created_at) values(?,?,?,?)", (trade["id"], json.dumps(trade), trade.get("status", "OPEN"), trade["created_at"]))
            db.commit()
        return trade

    async def update_trade(self, trade_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        data = {key: value for key, value in patch.items() if key in self.PG_UPDATE_FIELDS}
        if self.database_url:
            if not data:
                return await self._pg_query("select * from public.weeg_trades where id = %s", (trade_id,), fetch="one")
            assignments = ", ".join([f"{field} = %s" for field in data])
            values = [self._pg_value(field, data[field]) for field in data] + [trade_id]
            return await self._pg_query(f"update public.weeg_trades set {assignments} where id = %s returning *", values, fetch="one")
        if self.persistent_storage_configured:
            remote = await self._supabase("weeg_trades", method="PATCH", params={"id": f"eq.{trade_id}"}, data=patch)
            return remote[0] if remote else None
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("select payload from trades where id=?", (trade_id,)).fetchone()
            if not row:
                return None
            trade = {**json.loads(row[0]), **patch}
            db.execute("update trades set payload=?, status=? where id=?", (json.dumps(trade), trade.get("status", "OPEN"), trade_id))
            db.commit()
            return trade

    async def get_settings(self) -> dict[str, Any]:
        if self.database_url:
            row = await self._pg_query("select * from public.weeg_settings order by updated_at desc limit 1", fetch="one")
            return row or {}
        try:
            remote = await self._supabase("weeg_settings", params={"select": "*", "limit": "1"})
            if remote:
                return remote[0]
        except Exception:
            pass
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("select payload from settings where id=1").fetchone()
            return json.loads(row[0]) if row else {}

    async def save_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        if self.database_url:
            current = await self.get_settings()
            merged = {**current, **settings}
            symbols = merged.get("symbols") or []
            fields = ["symbols", "macro_timeframe", "trend_timeframe", "confirmation_timeframe", "execution_timeframe", "risk_per_trade", "minimum_rr", "confidence_threshold"]
            values = [merged.get("symbols", symbols), merged.get("macro_timeframe", "4h"), merged.get("trend_timeframe", "1h"), merged.get("confirmation_timeframe", "15m"), merged.get("execution_timeframe", "5m"), merged.get("risk_per_trade", 0.005), merged.get("minimum_rr", 2.0), merged.get("confidence_threshold", 65)]
            current_id = current.get("id")
            if current_id:
                assignments = ", ".join([f"{field} = %s" for field in fields])
                return await self._pg_query(f"update public.weeg_settings set {assignments}, updated_at = now() where id = %s returning *", values + [current_id], fetch="one")
            placeholders = ", ".join(["%s"] * len(fields))
            return await self._pg_query(f"insert into public.weeg_settings ({', '.join(fields)}) values ({placeholders}) returning *", values, fetch="one")
        try:
            remote = await self._supabase("weeg_settings", method="POST", data=settings)
            if remote:
                return remote[0]
        except Exception:
            pass
        with sqlite3.connect(self.db_path) as db:
            db.execute("insert or replace into settings(id,payload) values(1,?)", (json.dumps(settings),))
            db.commit()
        return settings

    async def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "postgres_configured": self.postgres_configured,
            "supabase_key_count": self.supabase_key_count,
            "persistent": self.has_persistent_storage,
            "last_error": self.storage_last_error,
        }
