import asyncio
import json
import sqlite3
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
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
        "signal_candle_time", "signal_age_seconds", "market_data_asof", "signal_price",
        "entry_deviation_pct", "monitor_entry_limit_pct", "expected_rr_after_execution",
        "reversal_risk", "reversal_risk_components", "overextension_metrics",
        "exit_checked_at", "stop_moved_to_breakeven",
    }
    PG_UPDATE_FIELDS = {
        "status", "result", "pnl", "max_favorable_excursion", "max_adverse_excursion",
        "exit_reason", "exit_price", "closed_at", "exit_checked_at", "stop_moved_to_breakeven",
    }

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
            db.execute("create table if not exists push_subscriptions (endpoint text primary key, p256dh text not null, auth text not null, expiration_time real, user_agent text, created_at text not null, updated_at text not null, user_id text)")
            try:
                db.execute("alter table push_subscriptions add column user_id text")
            except sqlite3.OperationalError:
                pass
            db.execute("create table if not exists user_settings (user_id text primary key, payload text not null, updated_at text not null)")
            db.execute("create table if not exists shadow_signals (id text primary key, payload text not null, created_at text not null)")
            db.execute("create table if not exists ifvg_configs (id text primary key, user_id text, strategy_id text not null, config_version text not null, enabled integer not null, payload text not null, updated_at text not null)")
            db.execute("create table if not exists ifvg_snapshots (id text primary key, user_id text, symbol text not null, decision_time text not null, content_hash text not null unique, payload text not null, created_at text not null)")
            db.execute("create table if not exists ifvg_setups (id text primary key, user_id text, symbol text not null, state text not null, source_fvg_id text not null, inversion_time text, payload text not null, created_at text not null, updated_at text not null)")
            db.execute("create table if not exists ifvg_trades (id text primary key, user_id text, setup_id text not null unique, symbol text not null, state text not null, payload text not null, created_at text not null, updated_at text not null)")
            db.execute("create table if not exists ifvg_state_events (id integer primary key autoincrement, setup_id text, trade_id text, to_state text not null, event_time text not null, payload text not null, created_at text not null)")
            db.execute("create table if not exists ifvg_fills (id integer primary key autoincrement, trade_id text not null, fill_role text not null, fill_sequence integer not null, event_time text not null, payload text not null, created_at text not null, unique(trade_id, fill_role, fill_sequence))")
            db.execute("create table if not exists ifvg_reservations (id text primary key, user_id text, strategy_id text not null, reservation_key text not null unique, symbol text not null, status text not null, payload text not null, created_at text not null, released_at text)")
            db.execute("create unique index if not exists ifvg_setups_idempotency_uq on ifvg_setups(symbol, source_fvg_id, ifnull(inversion_time, ''))")
            db.execute("create unique index if not exists ifvg_trades_active_symbol_uq on ifvg_trades(symbol) where state in ('ENTRY_ELIGIBLE','ORDER_INTENT','ORDER_SUBMITTED','ORDER_PARTIALLY_FILLED','ORDER_FILLED','POSITION_OPEN')")
            db.execute("create unique index if not exists ifvg_reservations_active_symbol_uq on ifvg_reservations(symbol) where status = 'ACTIVE'")
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
            result = await asyncio.wait_for(asyncio.to_thread(run_query), timeout=20)
            return self._json_safe(result)
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
                json=self._json_safe(data),
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

    async def list_trades(self, status: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
        if self.database_url:
            query = "select * from public.weeg_trades"
            params: list[Any] = []
            conditions: list[str] = []
            if status == "CLOSED_OR_STOPPED":
                conditions.append("status in ('CLOSED','STOPPED')")
            elif status:
                conditions.append("status = %s")
                params.append(status)
            if user_id:
                conditions.append("(user_id is null or user_id = %s)")
                params.append(user_id)
            if conditions:
                query += " where " + " and ".join(conditions)
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
            if user_id:
                params["or"] = f"(user_id.is.null,user_id.eq.{user_id})"
            remote = await self._supabase("weeg_trades", params=params)
            if remote is not None:
                return self._decorate_trades(remote)
        except Exception:
            if self.persistent_storage_configured:
                raise
        with sqlite3.connect(self.db_path) as db:
            query = "select payload from trades"
            args = []
            conditions = []
            if status == "CLOSED_OR_STOPPED":
                conditions.append("status in ('CLOSED','STOPPED')")
            elif status:
                conditions.append("status=?")
                args.append(status)
            if user_id:
                conditions.append("(json_extract(payload, '$.user_id') is null or json_extract(payload, '$.user_id')=?)")
                args.append(user_id)
            if conditions:
                query += " where " + " and ".join(conditions)
            query += " order by created_at desc limit 200"
            return self._decorate_trades([json.loads(row[0]) for row in db.execute(query, args).fetchall()])

    async def list_active_trades(self, user_id: str | None = None) -> list[dict[str, Any]]:
        if self.database_url:
            query = "select * from public.weeg_trades where status in ('PENDING','OPEN','PARTIAL')"
            params: list[Any] = []
            if user_id:
                query += " and (user_id is null or user_id = %s)"
                params.append(user_id)
            query += " order by created_at asc limit 500"
            try:
                return await self._pg_query(query, params)
            except Exception:
                if self.persistent_storage_ready and self.storage_key_source == "postgres":
                    raise
        try:
            params = {
                "select": "*",
                "status": "in.(PENDING,OPEN,PARTIAL)",
                "order": "created_at.asc",
                "limit": "500",
            }
            if user_id:
                params["or"] = f"(user_id.is.null,user_id.eq.{user_id})"
            remote = await self._supabase("weeg_trades", params=params)
            if remote is not None:
                return remote
        except Exception:
            if self.persistent_storage_configured:
                raise
        with sqlite3.connect(self.db_path) as db:
            if user_id:
                rows = db.execute("select payload from trades where status in ('PENDING','OPEN','PARTIAL') and (json_extract(payload, '$.user_id') is null or json_extract(payload, '$.user_id')=?) order by created_at asc limit 500", (user_id,)).fetchall()
            else:
                rows = db.execute("select payload from trades where status in ('PENDING','OPEN','PARTIAL') order by created_at asc limit 500").fetchall()
            return [json.loads(row[0]) for row in rows]

    async def upsert_push_subscription(self, subscription: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        data = {
            "endpoint": str(subscription["endpoint"]),
            "p256dh": str(subscription["p256dh"]),
            "auth": str(subscription["auth"]),
            "user_id": user_id,
            "expiration_time": subscription.get("expiration_time"),
            "user_agent": subscription.get("user_agent"),
            "updated_at": now,
            "created_at": now,
        }
        if self.database_url:
            query = """
                insert into public.weeg_push_subscriptions
                    (endpoint, p256dh, auth, user_id, expiration_time, user_agent, created_at, updated_at)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (endpoint) do update set
                    p256dh = excluded.p256dh,
                    auth = excluded.auth,
                    expiration_time = excluded.expiration_time,
                    user_agent = excluded.user_agent,
                    user_id = excluded.user_id,
                    updated_at = excluded.updated_at
                returning *
            """
            return await self._pg_query(query, (data["endpoint"], data["p256dh"], data["auth"], data["user_id"], data["expiration_time"], data["user_agent"], data["created_at"], data["updated_at"]), fetch="one")
        if self.persistent_storage_configured:
            remote = await self._supabase("weeg_push_subscriptions", method="POST", params={"on_conflict": "endpoint"}, data=data)
            if remote:
                return remote[0]
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "insert or replace into push_subscriptions(endpoint,p256dh,auth,expiration_time,user_agent,created_at,updated_at,user_id) values(?,?,?,?,?,?,?,?)",
                (data["endpoint"], data["p256dh"], data["auth"], data["expiration_time"], data["user_agent"], data["created_at"], data["updated_at"], data["user_id"]),
            )
            db.commit()
        return data

    async def list_push_subscriptions(self, user_id: str | None = None) -> list[dict[str, Any]]:
        if self.database_url:
            if user_id:
                return await self._pg_query("select endpoint, p256dh, auth, expiration_time, user_agent from public.weeg_push_subscriptions where user_id = %s order by updated_at desc limit 100", (user_id,))
            return await self._pg_query("select endpoint, p256dh, auth, expiration_time, user_agent from public.weeg_push_subscriptions order by updated_at desc limit 100")
        if self.persistent_storage_configured:
            try:
                params = {"select": "endpoint,p256dh,auth,expiration_time,user_agent", "order": "updated_at.desc", "limit": "100"}
                if user_id:
                    params["user_id"] = f"eq.{user_id}"
                remote = await self._supabase("weeg_push_subscriptions", params=params)
                if remote is not None:
                    return remote
            except Exception:
                if self.persistent_storage_configured:
                    raise
        with sqlite3.connect(self.db_path) as db:
            if user_id:
                rows = db.execute("select endpoint,p256dh,auth,expiration_time,user_agent from push_subscriptions where user_id=? order by updated_at desc limit 100", (user_id,)).fetchall()
            else:
                rows = db.execute("select endpoint,p256dh,auth,expiration_time,user_agent from push_subscriptions order by updated_at desc limit 100").fetchall()
            return [dict(zip(("endpoint", "p256dh", "auth", "expiration_time", "user_agent"), row)) for row in rows]

    async def delete_push_subscription(self, endpoint: str, user_id: str | None = None) -> bool:
        if self.database_url:
            if user_id:
                row = await self._pg_query("delete from public.weeg_push_subscriptions where endpoint = %s and user_id = %s returning endpoint", (endpoint, user_id), fetch="one")
            else:
                row = await self._pg_query("delete from public.weeg_push_subscriptions where endpoint = %s returning endpoint", (endpoint,), fetch="one")
            return bool(row)
        if self.persistent_storage_configured:
            try:
                params = {"endpoint": f"eq.{endpoint}"}
                if user_id:
                    params["user_id"] = f"eq.{user_id}"
                await self._supabase("weeg_push_subscriptions", method="DELETE", params=params)
                return True
            except Exception:
                if self.persistent_storage_configured:
                    raise
        with sqlite3.connect(self.db_path) as db:
            if user_id:
                cursor = db.execute("delete from push_subscriptions where endpoint=? and user_id=?", (endpoint, user_id))
            else:
                cursor = db.execute("delete from push_subscriptions where endpoint=?", (endpoint,))
            db.commit()
            return cursor.rowcount > 0

    async def find_auto_trade_signal(self, symbol: str, timeframe: str, signal_candle_time: str | None) -> dict[str, Any] | None:
        if not signal_candle_time:
            return None
        if self.database_url:
            query = """
                select * from public.weeg_trades
                where symbol = %s and timeframe = %s and auto_created = true
                  and signal_candle_time = %s
                order by created_at desc limit 1
            """
            try:
                return await self._pg_query(query, (symbol.upper(), timeframe, signal_candle_time), fetch="one")
            except Exception:
                if self.persistent_storage_ready and self.storage_key_source == "postgres":
                    raise
        try:
            remote = await self._supabase("weeg_trades", params={
                "select": "*", "symbol": f"eq.{symbol.upper()}", "timeframe": f"eq.{timeframe}",
                "auto_created": "eq.true", "signal_candle_time": f"eq.{signal_candle_time}",
                "order": "created_at.desc", "limit": "1",
            })
            if remote:
                return remote[0]
        except Exception:
            if self.persistent_storage_configured:
                raise
        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "select payload from trades where json_extract(payload, '$.symbol')=? and json_extract(payload, '$.timeframe')=? and json_extract(payload, '$.auto_created')=1 and json_extract(payload, '$.signal_candle_time')=? order by created_at desc limit 1",
                (symbol.upper(), timeframe, signal_candle_time),
            ).fetchone()
            return json.loads(row[0]) if row else None

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
            if self.persistent_storage_configured:
                raise
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
        if field in {"signal_reasons", "mtf_vetoes", "mtf_timeframes", "reversal_risk_components", "overextension_metrics", "blocked_reasons", "warning_reasons", "payload", "failed_gates", "metadata", "fill_model", "config_snapshot"}:
            try:
                from psycopg.types.json import Jsonb
                return Jsonb(Store._json_safe(value if value is not None else []))
            except ImportError:
                return json.dumps(Store._json_safe(value if value is not None else []), separators=(",", ":"))
        return value

    async def create_shadow_signal(self, signal: dict[str, Any]) -> dict[str, Any]:
        data = dict(signal)
        json_fields = {"reversal_risk_components", "overextension_metrics", "blocked_reasons", "warning_reasons"}
        if self.database_url:
            fields = [
                "id", "user_id", "symbol", "timeframe", "direction", "decision_time", "signal_candle_time",
                "signal_age_seconds", "market_data_asof", "signal_price", "simulated_entry_price",
                "simulated_stop_loss", "simulated_take_profit_1", "simulated_take_profit_2",
                "entry_deviation_pct", "monitor_entry_limit_pct", "expected_rr_after_execution", "regime",
                "mtf_alignment", "reversal_risk", "reversal_risk_components", "overextension_metrics",
                "would_have_executed", "would_block", "blocked_reasons", "warning_reasons", "outcome_status",
            ]
            values = [self._pg_value(field, data.get(field)) if field in json_fields else data.get(field) for field in fields]
            placeholders = ", ".join(["%s"] * len(fields))
            query = f"insert into public.weeg_shadow_signals ({', '.join(fields)}) values ({placeholders}) on conflict (symbol, timeframe, signal_candle_time) do update set decision_time = excluded.decision_time, signal_price = excluded.signal_price, simulated_entry_price = excluded.simulated_entry_price, simulated_stop_loss = excluded.simulated_stop_loss, simulated_take_profit_1 = excluded.simulated_take_profit_1, simulated_take_profit_2 = excluded.simulated_take_profit_2, entry_deviation_pct = excluded.entry_deviation_pct, monitor_entry_limit_pct = excluded.monitor_entry_limit_pct, expected_rr_after_execution = excluded.expected_rr_after_execution, regime = excluded.regime, mtf_alignment = excluded.mtf_alignment, reversal_risk = excluded.reversal_risk, reversal_risk_components = excluded.reversal_risk_components, overextension_metrics = excluded.overextension_metrics, would_have_executed = excluded.would_have_executed, would_block = excluded.would_block, blocked_reasons = excluded.blocked_reasons, warning_reasons = excluded.warning_reasons returning *"
            return await self._pg_query(query, values, fetch="one")
        if self.persistent_storage_configured:
            try:
                remote = await self._supabase("weeg_shadow_signals", method="POST", params={"on_conflict": "symbol,timeframe,signal_candle_time"}, data=data)
                if remote:
                    return remote[0]
            except Exception:
                if self.persistent_storage_configured:
                    raise
        now = datetime.now(timezone.utc).isoformat()
        data.setdefault("created_at", now)
        with sqlite3.connect(self.db_path) as db:
            db.execute("insert or replace into shadow_signals(id,payload,created_at) values(?,?,?)", (str(data["id"]), self._json_payload(data), data["created_at"]))
            db.commit()
        return data

    async def update_shadow_signal(self, signal_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"outcome_status", "outcome_pnl", "outcome_checked_at"}
        data = {key: value for key, value in patch.items() if key in allowed}
        if not data:
            return None
        if self.database_url:
            assignments = ", ".join([f"{field} = %s" for field in data])
            values = list(data.values()) + [signal_id]
            return await self._pg_query(f"update public.weeg_shadow_signals set {assignments} where id = %s returning *", values, fetch="one")
        if self.persistent_storage_configured:
            try:
                remote = await self._supabase("weeg_shadow_signals", method="PATCH", params={"id": f"eq.{signal_id}"}, data=data)
                return remote[0] if remote else None
            except Exception:
                if self.persistent_storage_configured:
                    raise
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("select payload from shadow_signals where id=?", (signal_id,)).fetchone()
            if not row:
                return None
            current = {**json.loads(row[0]), **data}
            db.execute("update shadow_signals set payload=? where id=?", (self._json_payload(current), signal_id))
            db.commit()
            return current

    async def list_shadow_signals(self, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        if self.database_url:
            return await self._pg_query("select * from public.weeg_shadow_signals order by decision_time desc limit %s", (limit,))
        if self.persistent_storage_configured:
            try:
                remote = await self._supabase("weeg_shadow_signals", params={"select": "*", "order": "decision_time.desc", "limit": str(limit)})
                if remote is not None:
                    return remote
            except Exception:
                if self.persistent_storage_configured:
                    raise
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("select payload from shadow_signals order by created_at desc limit ?", (limit,)).fetchall()
            return [json.loads(row[0]) for row in rows]

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
            db.execute("insert or replace into trades(id,payload,status,created_at) values(?,?,?,?)", (trade["id"], self._json_payload(trade), trade.get("status", "OPEN"), trade["created_at"]))
            db.commit()
        return trade

    async def update_trade(self, trade_id: str, patch: dict[str, Any], user_id: str | None = None) -> dict[str, Any] | None:
        data = {key: value for key, value in patch.items() if key in self.PG_UPDATE_FIELDS}
        if self.database_url:
            if not data:
                return None
            assignments = ", ".join([f"{field} = %s" for field in data])
            values = [self._pg_value(field, data[field]) for field in data] + [trade_id]
            where = "id = %s"
            if user_id:
                where += " and user_id = %s"
                values.append(user_id)
            return await self._pg_query(f"update public.weeg_trades set {assignments} where {where} returning *", values, fetch="one")
        if self.persistent_storage_configured:
            params = {"id": f"eq.{trade_id}"}
            if user_id:
                params["user_id"] = f"eq.{user_id}"
            remote = await self._supabase("weeg_trades", method="PATCH", params=params, data=patch)
            return remote[0] if remote else None
        with sqlite3.connect(self.db_path) as db:
            if user_id:
                row = db.execute("select payload from trades where id=? and json_extract(payload, '$.user_id')=?", (trade_id, user_id)).fetchone()
            else:
                row = db.execute("select payload from trades where id=?", (trade_id,)).fetchone()
            if not row:
                return None
            trade = {**json.loads(row[0]), **patch}
            db.execute("update trades set payload=?, status=? where id=?", (self._json_payload(trade), trade.get("status", "OPEN"), trade_id))
            db.commit()
            return trade

    @staticmethod
    def _json_safe(value: Any) -> Any:
        """Convert database/native values into JSON-safe primitives recursively."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (uuid.UUID,)):
            return str(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {str(key): Store._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [Store._json_safe(item) for item in value]
        return str(value)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _json_payload(value: Any) -> str:
        return json.dumps(Store._json_safe(value), separators=(",", ":"))

    async def create_ifvg_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        data = {"id": snapshot.get("id") or str(uuid.uuid4()), "strategy_id": "IFVG_SPOT_V1_2", **snapshot}
        data.setdefault("created_at", self._now_iso())
        if self.database_url:
            fields = ["id", "user_id", "strategy_id", "symbol", "decision_time", "data_asof", "config_version", "content_hash", "payload", "created_at"]
            values = [data.get(field) if field != "payload" else self._pg_value(field, data.get(field, {})) for field in fields]
            query = f"insert into public.ifvg_snapshots ({', '.join(fields)}) values ({', '.join(['%s'] * len(fields))}) on conflict (content_hash) do update set payload = excluded.payload returning *"
            return await self._pg_query(query, values, fetch="one")
        if self.persistent_storage_configured:
            remote = await self._supabase("ifvg_snapshots", method="POST", params={"on_conflict": "content_hash"}, data=data)
            if remote:
                return remote[0]
        with sqlite3.connect(self.db_path) as db:
            db.execute("insert or replace into ifvg_snapshots(id,user_id,symbol,decision_time,content_hash,payload,created_at) values(?,?,?,?,?,?,?)", (data["id"], data.get("user_id"), data["symbol"], data["decision_time"], data["content_hash"], self._json_payload(data), data["created_at"]))
            db.commit()
        return data

    async def create_ifvg_setup(self, setup: dict[str, Any]) -> dict[str, Any]:
        data = {"id": setup.get("id") or str(uuid.uuid4()), "strategy_id": "IFVG_SPOT_V1_2", **setup}
        data.setdefault("created_at", self._now_iso()); data.setdefault("updated_at", data["created_at"])
        if self.database_url:
            fields = ["id", "user_id", "strategy_id", "symbol", "source_fvg_id", "state", "state_version", "direction", "zone_low", "zone_high", "sweep_time", "inversion_time", "retest_start_time", "expires_at", "setup_snapshot_id", "config_version", "score", "score_version", "failed_gates", "metadata", "created_at", "updated_at"]
            values = [self._pg_value(field, data.get(field, [] if field == "failed_gates" else {})) if field in {"failed_gates", "metadata"} else data.get(field) for field in fields]
            query = f"insert into public.ifvg_setups ({', '.join(fields)}) values ({', '.join(['%s'] * len(fields))}) on conflict (strategy_id, symbol, source_fvg_id, inversion_time) do update set state = excluded.state, state_version = public.ifvg_setups.state_version + 1, score = excluded.score, failed_gates = excluded.failed_gates, metadata = excluded.metadata, updated_at = now() returning *"
            return await self._pg_query(query, values, fetch="one")
        if self.persistent_storage_configured:
            remote = await self._supabase("ifvg_setups", method="POST", params={"on_conflict": "strategy_id,symbol,source_fvg_id,inversion_time"}, data=data)
            if remote:
                return remote[0]
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("select payload from ifvg_setups where symbol=? and source_fvg_id=? and ifnull(inversion_time,'')=ifnull(?, '')", (data["symbol"], data["source_fvg_id"], data.get("inversion_time"))).fetchone()
            if row:
                current = {**json.loads(row[0]), **data, "state_version": int(json.loads(row[0]).get("state_version", 1)) + 1, "updated_at": self._now_iso()}
                db.execute("update ifvg_setups set state=?,payload=?,updated_at=? where id=?", (current.get("state"), self._json_payload(current), current["updated_at"], current["id"]))
                db.commit(); return current
            db.execute("insert into ifvg_setups(id,user_id,symbol,state,source_fvg_id,inversion_time,payload,created_at,updated_at) values(?,?,?,?,?,?,?,?,?)", (data["id"], data.get("user_id"), data["symbol"], data.get("state"), data["source_fvg_id"], data.get("inversion_time"), self._json_payload(data), data["created_at"], data["updated_at"]))
            db.commit()
        return data

    async def list_ifvg_setups(self, state: str | None = None, symbol: str | None = None, user_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        if self.database_url:
            conditions, params = ["strategy_id = 'IFVG_SPOT_V1_2'"], []
            if state: conditions.append("state = %s"); params.append(state)
            if symbol: conditions.append("symbol = %s"); params.append(symbol.upper())
            if user_id: conditions.append("(user_id is null or user_id = %s)"); params.append(user_id)
            return await self._pg_query(f"select * from public.ifvg_setups where {' and '.join(conditions)} order by updated_at desc limit %s", params + [limit])
        if self.persistent_storage_configured:
            params = {"select": "*", "strategy_id": "eq.IFVG_SPOT_V1_2", "order": "updated_at.desc", "limit": str(limit)}
            if state: params["state"] = f"eq.{state}"
            if symbol: params["symbol"] = f"eq.{symbol.upper()}"
            if user_id: params["or"] = f"(user_id.is.null,user_id.eq.{user_id})"
            remote = await self._supabase("ifvg_setups", params=params)
            if remote is not None: return remote
        with sqlite3.connect(self.db_path) as db:
            if user_id:
                rows = db.execute("select payload from ifvg_setups where (? is null or state=?) and (? is null or symbol=?) and (user_id is null or user_id=?) order by updated_at desc limit ?", (state, state, symbol.upper() if symbol else None, symbol.upper() if symbol else None, user_id, limit)).fetchall()
            else:
                rows = db.execute("select payload from ifvg_setups where (? is null or state=?) and (? is null or symbol=?) order by updated_at desc limit ?", (state, state, symbol.upper() if symbol else None, symbol.upper() if symbol else None, limit)).fetchall()
            return [json.loads(row[0]) for row in rows]

    async def update_ifvg_setup(self, setup_id: str, patch: dict[str, Any], user_id: str | None = None) -> dict[str, Any] | None:
        allowed = {"state", "state_version", "retest_start_time", "expires_at", "score", "score_version", "failed_gates", "metadata", "updated_at"}
        data = {key: value for key, value in patch.items() if key in allowed}
        if not data: return None
        data.setdefault("updated_at", self._now_iso())
        if self.database_url:
            assignments = ", ".join(f"{key} = %s" for key in data)
            values = [self._pg_value(key, value) if key in {"failed_gates", "metadata"} else value for key, value in data.items()] + [setup_id]
            where = "id = %s" + (" and user_id = %s" if user_id else "")
            if user_id: values.append(user_id)
            return await self._pg_query(f"update public.ifvg_setups set {assignments} where {where} returning *", values, fetch="one")
        if self.persistent_storage_configured:
            params = {"id": f"eq.{setup_id}"}
            if user_id: params["user_id"] = f"eq.{user_id}"
            remote = await self._supabase("ifvg_setups", method="PATCH", params=params, data=data)
            return remote[0] if remote else None
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("select payload from ifvg_setups where id=?" + (" and user_id=?" if user_id else ""), (setup_id, user_id) if user_id else (setup_id,)).fetchone()
            if not row: return None
            current = {**json.loads(row[0]), **data}
            db.execute("update ifvg_setups set state=?, payload=?, updated_at=? where id=?", (current.get("state"), self._json_payload(current), current["updated_at"], setup_id)); db.commit(); return current

    async def create_ifvg_trade(self, trade: dict[str, Any]) -> dict[str, Any]:
        data = {"id": trade.get("id") or str(uuid.uuid4()), "strategy_id": "IFVG_SPOT_V1_2", **trade}
        data.setdefault("created_at", self._now_iso()); data.setdefault("updated_at", data["created_at"])
        if self.database_url:
            fields = ["id", "user_id", "strategy_id", "setup_id", "symbol", "direction", "state", "entry_reference", "entry_fill", "stop_price", "stop_fill", "target_price", "target_fill_gross", "exit_fill", "gross_rr", "net_rr", "risk_per_unit_quote", "risk_amount_quote", "quantity", "entry_fee_quote", "stop_fee_quote", "target_fee_quote", "exit_fee_quote", "realized_pnl_quote", "fill_model", "config_snapshot", "data_snapshot_id", "score", "score_version", "failed_gates", "decision_time", "opened_at", "closed_at", "exit_reason", "result", "metadata", "created_at", "updated_at"]
            values = [self._pg_value(field, data.get(field, {} if field in {"fill_model", "config_snapshot", "metadata"} else [])) if field in {"fill_model", "config_snapshot", "failed_gates", "metadata"} else data.get(field) for field in fields]
            query = f"insert into public.ifvg_trades ({', '.join(fields)}) values ({', '.join(['%s'] * len(fields))}) returning *"
            return await self._pg_query(query, values, fetch="one")
        if self.persistent_storage_configured:
            remote = await self._supabase("ifvg_trades", method="POST", data=data)
            if remote: return remote[0]
        with sqlite3.connect(self.db_path) as db:
            db.execute("insert into ifvg_trades(id,user_id,setup_id,symbol,state,payload,created_at,updated_at) values(?,?,?,?,?,?,?,?)", (data["id"], data.get("user_id"), data["setup_id"], data["symbol"], data.get("state"), self._json_payload(data), data["created_at"], data["updated_at"])); db.commit()
        return data

    async def list_ifvg_trades(self, state: str | None = None, user_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        if self.database_url:
            conditions, params = ["strategy_id = 'IFVG_SPOT_V1_2'"], []
            if state: conditions.append("state = %s"); params.append(state)
            if user_id: conditions.append("(user_id is null or user_id = %s)"); params.append(user_id)
            return await self._pg_query(f"select * from public.ifvg_trades where {' and '.join(conditions)} order by decision_time desc limit %s", params + [limit])
        if self.persistent_storage_configured:
            params = {"select": "*", "strategy_id": "eq.IFVG_SPOT_V1_2", "order": "decision_time.desc", "limit": str(limit)}
            if state: params["state"] = f"eq.{state}"
            if user_id: params["or"] = f"(user_id.is.null,user_id.eq.{user_id})"
            remote = await self._supabase("ifvg_trades", params=params)
            if remote is not None: return remote
        with sqlite3.connect(self.db_path) as db:
            if user_id:
                rows = db.execute("select payload from ifvg_trades where (? is null or state=?) and (user_id is null or user_id=?) order by created_at desc limit ?", (state, state, user_id, limit)).fetchall()
            else:
                rows = db.execute("select payload from ifvg_trades where (? is null or state=?) order by created_at desc limit ?", (state, state, limit)).fetchall()
            return [json.loads(row[0]) for row in rows]

    async def find_open_ifvg_trade(self, symbol: str, user_id: str | None = None) -> dict[str, Any] | None:
        active = "('ENTRY_ELIGIBLE','ORDER_INTENT','ORDER_SUBMITTED','ORDER_PARTIALLY_FILLED','ORDER_FILLED','POSITION_OPEN')"
        if self.database_url:
            query = f"select * from public.ifvg_trades where strategy_id = 'IFVG_SPOT_V1_2' and symbol = %s and state in {active}" + (" and (user_id is null or user_id = %s)" if user_id else "") + " order by decision_time desc limit 1"
            return await self._pg_query(query, (symbol.upper(), user_id) if user_id else (symbol.upper(),), fetch="one")
        if self.persistent_storage_configured:
            params = {"select": "*", "strategy_id": "eq.IFVG_SPOT_V1_2", "symbol": f"eq.{symbol.upper()}", "state": "in." + active.replace("'", ""), "limit": "1"}
            remote = await self._supabase("ifvg_trades", params=params)
            if remote: return remote[0]
        with sqlite3.connect(self.db_path) as db:
            if user_id:
                row = db.execute("select payload from ifvg_trades where symbol=? and state in ('ENTRY_ELIGIBLE','ORDER_INTENT','ORDER_SUBMITTED','ORDER_PARTIALLY_FILLED','ORDER_FILLED','POSITION_OPEN') and (user_id is null or user_id=?) order by created_at desc limit 1", (symbol.upper(), user_id)).fetchone()
            else:
                row = db.execute("select payload from ifvg_trades where symbol=? and state in ('ENTRY_ELIGIBLE','ORDER_INTENT','ORDER_SUBMITTED','ORDER_PARTIALLY_FILLED','ORDER_FILLED','POSITION_OPEN') order by created_at desc limit 1", (symbol.upper(),)).fetchone()
            return json.loads(row[0]) if row else None

    async def update_ifvg_trade(self, trade_id: str, patch: dict[str, Any], user_id: str | None = None) -> dict[str, Any] | None:
        allowed = {"state", "entry_fill", "stop_fill", "target_fill_gross", "exit_fill", "gross_rr", "net_rr", "quantity", "entry_fee_quote", "stop_fee_quote", "target_fee_quote", "exit_fee_quote", "realized_pnl_quote", "failed_gates", "opened_at", "closed_at", "exit_reason", "result", "metadata", "updated_at"}
        data = {key: value for key, value in patch.items() if key in allowed}; data.setdefault("updated_at", self._now_iso())
        if self.database_url:
            assignments = ", ".join(f"{key} = %s" for key in data); values = [self._pg_value(key, value) if key in {"failed_gates", "metadata"} else value for key, value in data.items()] + [trade_id]
            where = "id = %s" + (" and user_id = %s" if user_id else "")
            if user_id: values.append(user_id)
            return await self._pg_query(f"update public.ifvg_trades set {assignments} where {where} returning *", values, fetch="one")
        if self.persistent_storage_configured:
            params = {"id": f"eq.{trade_id}"};
            if user_id: params["user_id"] = f"eq.{user_id}"
            remote = await self._supabase("ifvg_trades", method="PATCH", params=params, data=data)
            return remote[0] if remote else None
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("select payload from ifvg_trades where id=?" + (" and user_id=?" if user_id else ""), (trade_id, user_id) if user_id else (trade_id,)).fetchone()
            if not row: return None
            current = {**json.loads(row[0]), **data}; db.execute("update ifvg_trades set state=?,payload=?,updated_at=? where id=?", (current.get("state"), self._json_payload(current), current["updated_at"], trade_id)); db.commit(); return current

    async def create_ifvg_state_event(self, event: dict[str, Any]) -> dict[str, Any]:
        data = {"strategy_id": "IFVG_SPOT_V1_2", **event}; data.setdefault("created_at", self._now_iso())
        if self.database_url:
            fields = ["setup_id", "trade_id", "from_state", "to_state", "reason_code", "reason_detail", "event_time", "candle_time", "data_snapshot_id", "metadata"]
            values = [self._pg_value(field, data.get(field, {})) if field == "metadata" else data.get(field) for field in fields]
            return await self._pg_query(f"insert into public.ifvg_state_events ({', '.join(fields)}) values ({', '.join(['%s'] * len(fields))}) returning *", values, fetch="one")
        if self.persistent_storage_configured:
            remote = await self._supabase("ifvg_state_events", method="POST", data=data)
            if remote: return remote[0]
        with sqlite3.connect(self.db_path) as db:
            cursor = db.execute("insert into ifvg_state_events(setup_id,trade_id,to_state,event_time,payload,created_at) values(?,?,?,?,?,?)", (data.get("setup_id"), data.get("trade_id"), data["to_state"], data["event_time"], self._json_payload(data), data["created_at"])); db.commit(); data["id"] = cursor.lastrowid
        return data

    async def create_ifvg_fill(self, fill: dict[str, Any]) -> dict[str, Any]:
        data = {"strategy_id": "IFVG_SPOT_V1_2", **fill}; data.setdefault("created_at", self._now_iso()); data.setdefault("fill_sequence", 1)
        if self.database_url:
            fields = ["trade_id", "fill_role", "fill_sequence", "reference_price", "executable_price", "quantity", "fee_quote", "fee_asset", "spread_component", "slippage_component", "latency_component", "event_time", "intent_time", "execution_time", "metadata"]
            values = [self._pg_value(field, data.get(field, {})) if field == "metadata" else data.get(field) for field in fields]
            return await self._pg_query(f"insert into public.ifvg_fills ({', '.join(fields)}) values ({', '.join(['%s'] * len(fields))}) on conflict (trade_id, fill_role, fill_sequence) do update set executable_price = excluded.executable_price, quantity = excluded.quantity, fee_quote = excluded.fee_quote returning *", values, fetch="one")
        if self.persistent_storage_configured:
            remote = await self._supabase("ifvg_fills", method="POST", params={"on_conflict": "trade_id,fill_role,fill_sequence"}, data=data)
            if remote: return remote[0]
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("select id from ifvg_fills where trade_id=? and fill_role=? and fill_sequence=?", (data["trade_id"], data["fill_role"], data["fill_sequence"])).fetchone()
            if row:
                data["id"] = row[0]
                db.execute("update ifvg_fills set payload=?,event_time=? where id=?", (self._json_payload(data), data["event_time"], row[0]))
            else:
                cursor = db.execute("insert into ifvg_fills(trade_id,fill_role,fill_sequence,event_time,payload,created_at) values(?,?,?,?,?,?)", (data["trade_id"], data["fill_role"], data["fill_sequence"], data["event_time"], self._json_payload(data), data["created_at"])); data["id"] = cursor.lastrowid
                db.execute("update ifvg_fills set payload=? where id=?", (self._json_payload(data), data["id"]))
            db.commit()
        return data

    async def list_ifvg_fills(self, trade_id: str, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        if self.database_url: return await self._pg_query("select * from public.ifvg_fills where trade_id = %s order by event_time asc limit %s", (trade_id, limit))
        if self.persistent_storage_configured:
            remote = await self._supabase("ifvg_fills", params={"select": "*", "trade_id": f"eq.{trade_id}", "order": "event_time.asc", "limit": str(limit)})
            if remote is not None: return remote
        with sqlite3.connect(self.db_path) as db: return [json.loads(row[0]) for row in db.execute("select payload from ifvg_fills where trade_id=? order by event_time asc limit ?", (trade_id, limit)).fetchall()]

    async def create_ifvg_reservation(self, reservation: dict[str, Any]) -> dict[str, Any] | None:
        data = {"id": reservation.get("id") or str(uuid.uuid4()), "strategy_id": "IFVG_SPOT_V1_2", **reservation}; data.setdefault("created_at", self._now_iso()); data.setdefault("status", "ACTIVE")
        if self.database_url:
            fields = ["id", "user_id", "strategy_id", "reservation_key", "symbol", "trade_id", "reserved_quantity", "reserved_quote", "reserved_risk_quote", "status", "expires_at", "metadata", "created_at", "released_at"]
            values = [self._pg_value(field, data.get(field, {})) if field == "metadata" else data.get(field) for field in fields]
            return await self._pg_query(f"insert into public.ifvg_reservations ({', '.join(fields)}) values ({', '.join(['%s'] * len(fields))}) on conflict (reservation_key) do nothing returning *", values, fetch="one")
        if self.persistent_storage_configured:
            remote = await self._supabase("ifvg_reservations", method="POST", params={"on_conflict": "reservation_key"}, data=data)
            return remote[0] if remote else None
        with sqlite3.connect(self.db_path) as db:
            try: db.execute("insert into ifvg_reservations(id,user_id,strategy_id,reservation_key,symbol,status,payload,created_at,released_at) values(?,?,?,?,?,?,?,?,?)", (data["id"], data.get("user_id"), data["strategy_id"], data["reservation_key"], data["symbol"], data["status"], self._json_payload(data), data["created_at"], data.get("released_at"))); db.commit(); return data
            except sqlite3.IntegrityError: return None

    async def update_ifvg_reservation(self, reservation_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        data = {key: value for key, value in patch.items() if key in {"status", "released_at", "metadata"}}
        if self.database_url:
            if not data: return None
            assignments = ", ".join(f"{key} = %s" for key in data); values = [self._pg_value(key, value) if key == "metadata" else value for key, value in data.items()] + [reservation_id]
            return await self._pg_query(f"update public.ifvg_reservations set {assignments} where id = %s returning *", values, fetch="one")
        if self.persistent_storage_configured:
            remote = await self._supabase("ifvg_reservations", method="PATCH", params={"id": f"eq.{reservation_id}"}, data=data); return remote[0] if remote else None
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("select payload from ifvg_reservations where id=?", (reservation_id,)).fetchone()
            if not row: return None
            current = {**json.loads(row[0]), **data}; db.execute("update ifvg_reservations set status=?,released_at=?,payload=? where id=?", (current.get("status"), current.get("released_at"), self._json_payload(current), reservation_id)); db.commit(); return current

    async def get_settings(self, user_id: str | None = None) -> dict[str, Any]:
        if self.database_url:
            if user_id:
                row = await self._pg_query("select * from public.weeg_settings where user_id = %s order by updated_at desc limit 1", (user_id,), fetch="one")
            else:
                row = await self._pg_query("select * from public.weeg_settings order by updated_at desc limit 1", fetch="one")
            return row or {}
        try:
            params = {"select": "*", "limit": "1"}
            if user_id:
                params["user_id"] = f"eq.{user_id}"
            remote = await self._supabase("weeg_settings", params=params)
            if remote:
                return remote[0]
        except Exception:
            if self.persistent_storage_configured:
                raise
        with sqlite3.connect(self.db_path) as db:
            if user_id:
                row = db.execute("select payload from user_settings where user_id=?", (user_id,)).fetchone()
            else:
                row = db.execute("select payload from settings where id=1").fetchone()
            return json.loads(row[0]) if row else {}

    async def save_settings(self, settings: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        if self.database_url:
            current = await self.get_settings(user_id=user_id)
            merged = {**current, **settings}
            symbols = merged.get("symbols") or []
            fields = ["symbols", "macro_timeframe", "trend_timeframe", "confirmation_timeframe", "execution_timeframe", "risk_per_trade", "minimum_rr", "confidence_threshold"]
            values = [merged.get("symbols", symbols), merged.get("macro_timeframe", "4h"), merged.get("trend_timeframe", "1h"), merged.get("confirmation_timeframe", "15m"), merged.get("execution_timeframe", "5m"), merged.get("risk_per_trade", 0.005), merged.get("minimum_rr", 2.0), merged.get("confidence_threshold", 65)]
            current_id = current.get("id")
            if current_id:
                assignments = ", ".join([f"{field} = %s" for field in fields])
                if user_id:
                    return await self._pg_query(f"update public.weeg_settings set {assignments}, updated_at = now() where id = %s and user_id = %s returning *", values + [current_id, user_id], fetch="one")
                return await self._pg_query(f"update public.weeg_settings set {assignments}, updated_at = now() where id = %s returning *", values + [current_id], fetch="one")
            if user_id:
                fields.insert(0, "user_id")
                values.insert(0, user_id)
            placeholders = ", ".join(["%s"] * len(fields))
            return await self._pg_query(f"insert into public.weeg_settings ({', '.join(fields)}) values ({placeholders}) returning *", values, fetch="one")
        try:
            remote_data = {**settings, **({"user_id": user_id} if user_id else {})}
            if user_id:
                existing = await self._supabase("weeg_settings", params={"select": "id", "user_id": f"eq.{user_id}", "limit": "1"})
                if existing:
                    remote = await self._supabase("weeg_settings", method="PATCH", params={"id": f"eq.{existing[0]['id']}", "user_id": f"eq.{user_id}"}, data=remote_data)
                else:
                    remote = await self._supabase("weeg_settings", method="POST", params={"on_conflict": "user_id"}, data=remote_data)
            else:
                remote = await self._supabase("weeg_settings", method="POST", data=remote_data)
            if remote:
                return remote[0]
        except Exception:
            if self.persistent_storage_configured:
                raise
        with sqlite3.connect(self.db_path) as db:
            if user_id:
                db.execute("insert or replace into user_settings(user_id,payload,updated_at) values(?,?,?)", (user_id, self._json_payload(settings), datetime.now(timezone.utc).isoformat()))
            else:
                db.execute("insert or replace into settings(id,payload) values(1,?)", (self._json_payload(settings)))
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
