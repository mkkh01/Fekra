from __future__ import annotations

import asyncio
import base64
import json
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
        self.supabase_write_enabled = False
        self._supabase_auth_error_logged = False
        self._init_supabase()
        self._init_redis()

    def _init_supabase(self) -> None:
        key = self.settings.supabase_server_key
        if not (self.settings.supabase_url and key):
            return
        if not self._looks_like_server_key(key):
            logger.warning("Supabase writes disabled: SUPABASE_SERVICE_ROLE_KEY or a service_role SUPABASE_KEY is required")
            return
        try:
            from supabase import create_client
            self.supabase = create_client(self.settings.supabase_url, key)
            self.supabase_write_enabled = True
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
                self.supabase_ok = self.supabase_write_enabled
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
        if self.supabase is None or not self.supabase_write_enabled:
            return
        payload = {"event_type": event_type, "message": message, "data": data or {}}
        try:
            await asyncio.to_thread(lambda: self.supabase.table("system_events").insert(payload).execute())
        except Exception as exc:
            self._mark_supabase_failure(exc, "event write")

    async def write_cycle(self, cycle: dict[str, Any]) -> None:
        if self.supabase is None or not self.supabase_write_enabled:
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
            "analysis_inputs": cycle.get("inputs", {}),
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
                "audience_facing_reasoning": "\n".join(cycle.get("workflow", [])),
                "evidence": decision.get("evidence", []),
                "counter_evidence": decision.get("counter_evidence", []),
                "alternative_hypotheses": decision.get("alternative_hypotheses", []),
                "uncertainty": decision.get("uncertainty"),
                "invalidation_context": decision.get("invalidating_context", []),
                "execution_status": "PAPER_NOT_EXECUTED",
                "scoring": {
                    **(decision.get("scoring", {}) or {}),
                    "market_bias": decision.get("market_bias", "NEUTRAL"),
                    "trade_decision": decision.get("trade_decision", "WAIT"),
                    "confidence": decision.get("confidence", 0),
                    "data_quality": decision.get("data_quality", 0),
                    "market_regime": decision.get("market_regime", "UNCERTAIN"),
                    "trigger_status": decision.get("trigger_status", {}),
                    "invalidation": decision.get("invalidation", {}),
                    "rejection_reasons": decision.get("rejection_reasons", []),
                    "alternative_scenarios": decision.get("alternative_scenarios", []),
                },
            }
            await asyncio.to_thread(lambda: self.supabase.table("brain_decisions").insert(decision_payload).execute())
        except Exception as exc:
            self._mark_supabase_failure(exc, "cycle write")

    async def update_cycle_performance(self, cycle: dict[str, Any]) -> None:
        if self.supabase is None or not self.supabase_write_enabled or not cycle.get("performance"):
            return
        try:
            payload = {"analysis_inputs": cycle.get("inputs", {})}
            await asyncio.to_thread(lambda: self.supabase.table("brain_cycles").update(payload).eq("id", cycle["id"]).execute())
        except Exception as exc:
            self._mark_supabase_failure(exc, "cycle performance update")

    async def write_news(self, item: dict[str, Any]) -> None:
        if self.supabase is None or not self.supabase_write_enabled:
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
            self._mark_supabase_failure(exc, "news write")

    @staticmethod
    def _looks_like_server_key(key: str) -> bool:
        if key.startswith("sb_secret_"):
            return True
        if not key.startswith("eyJ"):
            return False
        try:
            payload = key.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
            return claims.get("role") == "service_role"
        except (IndexError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return False

    def _mark_supabase_failure(self, exc: Exception, operation: str) -> None:
        message = str(exc)
        permission_failure = "403" in message or "permission denied" in message.lower() or "insufficient_privilege" in message.lower()
        auth_failure = "401" in message or "Invalid API key" in message or "Unauthorized" in message
        if auth_failure or permission_failure:
            self.supabase_write_enabled = False
            if not self._supabase_auth_error_logged:
                reason = "permission" if permission_failure else "authentication"
                logger.error("Supabase %s failed during %s; disabling further Supabase writes until restart", reason, operation)
                self._supabase_auth_error_logged = True
        else:
            logger.warning("Supabase %s failed: %s", operation, exc)

    async def cache_snapshot(self, key: str, value: dict[str, Any], ttl: int = 60) -> None:
        if self.redis is None:
            return
        try:
            import json
            await self.redis.set(key, json.dumps(value), ex=ttl)
        except Exception as exc:
            logger.warning("Redis cache write failed: %s", exc)
