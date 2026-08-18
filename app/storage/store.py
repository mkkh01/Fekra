from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


class StorageManager:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.supabase = None
        self.redis = None
        self.supabase_ok = False
        self.redis_ok = False
        self._init_supabase()
        self._init_redis()

    def _init_supabase(self) -> None:
        if not (self.settings.supabase_url and self.settings.supabase_key):
            return
        try:
            from supabase import create_client
            self.supabase = create_client(self.settings.supabase_url, self.settings.supabase_key)
        except Exception as exc:
            logger.warning("Supabase client initialization failed: %s", exc)

    def _init_redis(self) -> None:
        if not self.settings.redis_url:
            return
        try:
            from redis.asyncio import Redis
            self.redis = Redis.from_url(self.settings.redis_url, decode_responses=True)
        except Exception as exc:
            logger.warning("Redis client initialization failed: %s", exc)

    async def check(self) -> None:
        if self.supabase is not None:
            try:
                await asyncio.to_thread(lambda: self.supabase.table("assets").select("symbol").limit(1).execute())
                self.supabase_ok = True
            except Exception as exc:
                self.supabase_ok = False
                logger.warning("Supabase health check failed: %s", exc)
        if self.redis is not None:
            try:
                self.redis_ok = bool(await self.redis.ping())
            except Exception as exc:
                self.redis_ok = False
                logger.warning("Redis health check failed: %s", exc)

    async def close(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()

    async def write_event(self, event_type: str, message: str, data: dict[str, Any] | None = None) -> None:
        if self.supabase is None:
            return
        payload = {"event_type": event_type, "message": message, "data": data or {}}
        try:
            await asyncio.to_thread(lambda: self.supabase.table("system_events").insert(payload).execute())
        except Exception as exc:
            logger.warning("Supabase event write failed: %s", exc)

    async def write_cycle(self, cycle: dict[str, Any]) -> None:
        if self.supabase is None:
            return
        payload = {
            "id": cycle["id"],
            "started_at": cycle["started_at"],
            "finished_at": cycle.get("finished_at"),
            "trigger_type": cycle.get("trigger_type", "unknown"),
            "trigger_symbol": cycle.get("trigger_symbol"),
            "status": cycle.get("status", "COMPLETED"),
            "objective": cycle.get("objective"),
            "summary": cycle.get("summary"),
            "final_decision": cycle.get("final_decision"),
            "model": cycle.get("model"),
        }
        try:
            await asyncio.to_thread(lambda: self.supabase.table("brain_cycles").upsert(payload).execute())
            decision = cycle.get("decision") or {}
            decision_payload = {
                "cycle_id": cycle["id"],
                "symbol": cycle.get("trigger_symbol"),
                "action": decision.get("action", "WAIT"),
                "thesis": decision.get("thesis"),
                "summary": decision.get("summary"),
                "evidence": decision.get("evidence", []),
                "counter_evidence": decision.get("counter_evidence", []),
                "alternative_hypotheses": decision.get("alternative_hypotheses", []),
                "uncertainty": decision.get("uncertainty"),
                "invalidation_context": decision.get("invalidating_context", []),
                "execution_status": "PAPER_NOT_EXECUTED",
            }
            await asyncio.to_thread(lambda: self.supabase.table("brain_decisions").insert(decision_payload).execute())
        except Exception as exc:
            logger.warning("Supabase cycle write failed: %s", exc)

    async def write_news(self, item: dict[str, Any]) -> None:
        if self.supabase is None:
            return
        payload = {
            "fingerprint": item["id"],
            "title": item["title"],
            "url": item["url"],
            "summary": item.get("summary"),
            "source": item["source"],
            "published_at": item.get("published_at"),
            "retrieved_at": item.get("retrieved_at") or datetime.now(timezone.utc).isoformat(),
            "evidence_status": item.get("evidence_status", "sourced"),
            "metadata": {"symbols": item.get("symbols", [])},
        }
        try:
            await asyncio.to_thread(lambda: self.supabase.table("news").upsert(payload, on_conflict="fingerprint").execute())
        except Exception as exc:
            logger.warning("Supabase news write failed: %s", exc)

    async def cache_snapshot(self, key: str, value: dict[str, Any], ttl: int = 60) -> None:
        if self.redis is None:
            return
        try:
            import json
            await self.redis.set(key, json.dumps(value), ex=ttl)
        except Exception as exc:
            logger.warning("Redis cache write failed: %s", exc)
