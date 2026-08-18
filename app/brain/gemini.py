from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from google import genai

from app.config.settings import get_settings
from app.state import RuntimeState

logger = logging.getLogger(__name__)
UTC = timezone.utc


@dataclass
class GeminiKeyState:
    index: int
    key: str
    requests: int = 0
    successes: int = 0
    failures: int = 0
    cooldown_until: datetime | None = None
    last_error: str | None = None
    last_error_category: str | None = None
    diagnostic_requests: int = 0
    diagnostic_successes: int = 0
    diagnostic_failures: int = 0
    diagnostic_checked_at: str | None = None

    @property
    def ready(self) -> bool:
        return self.cooldown_until is None or datetime.now(UTC) >= self.cooldown_until

    @property
    def masked(self) -> str:
        if len(self.key) <= 8:
            return f"account-{self.index}"
        return f"{self.key[:4]}…{self.key[-4:]}"


class GeminiKeyPool:
    def __init__(self, state: RuntimeState) -> None:
        self.state = state
        self.model = get_settings().gemini_model
        self.keys = [GeminiKeyState(index=i + 1, key=value) for i, value in enumerate(get_settings().gemini_keys)]
        self.active_index = 0
        self._lock = asyncio.Lock()
        self._sync_metrics()

    @property
    def configured(self) -> bool:
        return bool(self.keys)

    async def analyze(self, prompt: str, system_instruction: str = "") -> dict[str, Any]:
        if not self.keys:
            return {"ok": False, "error": "GEMINI_API_KEY or GEMINI_API_KEY_1..5 is not configured"}
        async with self._lock:
            ordered = self._ready_order()
            if not ordered:
                return {"ok": False, "error": "All Gemini accounts are temporarily cooling down"}
            for position, key_state in enumerate(ordered):
                self.active_index = self.keys.index(key_state)
                key_state.requests += 1
                self._sync_metrics()
                try:
                    text = await self._request(key_state.key, prompt, system_instruction)
                    key_state.successes += 1
                    key_state.last_error = None
                    key_state.last_error_category = None
                    self._sync_metrics()
                    return {
                        "ok": True,
                        "text": text,
                        "model": self.model,
                        "account_index": key_state.index,
                    }
                except Exception as exc:
                    key_state.failures += 1
                    key_state.last_error = str(exc)[:300]
                    key_state.last_error_category = self._classify_error(exc)
                    cooldown_seconds = 900 if key_state.last_error_category == "MODEL_UNAVAILABLE" else min(60 * key_state.failures, 900)
                    key_state.cooldown_until = datetime.now(UTC) + timedelta(seconds=cooldown_seconds)
                    self.state.gemini_usage["rotations_total"] += 1
                    self._sync_metrics()
                    self.state.event("GEMINI", "Gemini account failed; rotating to next account", {"account_index": key_state.index})
                    if position == len(ordered) - 1:
                        return {"ok": False, "error": "All configured Gemini accounts failed"}
        return {"ok": False, "error": "Gemini request failed"}

    async def probe_all(self) -> dict[str, Any]:
        """Probe every configured key once with a minimal JSON request, without changing trading decisions."""
        if not self.keys:
            return {"model": self.model, "checked": 0, "results": []}
        results: list[dict[str, Any]] = []
        async with self._lock:
            for key_state in self.keys:
                key_state.diagnostic_requests += 1
                key_state.diagnostic_checked_at = datetime.now(UTC).isoformat()
                try:
                    text = await self._request(key_state.key, '{"probe":"return ok"}', 'Return JSON only: {"probe":"ok"}.')
                    key_state.diagnostic_successes += 1
                    key_state.last_error = None
                    key_state.last_error_category = None
                    results.append({"account_index": key_state.index, "status": "OK", "model": self.model, "response_valid": bool(text)})
                except Exception as exc:
                    key_state.diagnostic_failures += 1
                    key_state.last_error = str(exc)[:300]
                    key_state.last_error_category = self._classify_error(exc)
                    results.append({"account_index": key_state.index, "status": "FAILED", "model": self.model, "error_category": key_state.last_error_category, "last_error": key_state.last_error})
            self._sync_metrics()
        return {"model": self.model, "checked": len(results), "successful": sum(item["status"] == "OK" for item in results), "failed": sum(item["status"] == "FAILED" for item in results), "results": results}

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        text = str(exc).upper()
        if "404" in text or "NOT_FOUND" in text or "MODEL" in text and "AVAILABLE" in text:
            return "MODEL_UNAVAILABLE"
        if "429" in text or "RESOURCE_EXHAUSTED" in text or "QUOTA" in text or "RATE" in text and "LIMIT" in text:
            return "QUOTA_OR_RATE_LIMIT"
        if "401" in text or "403" in text or "UNAUTHENTICATED" in text or "PERMISSION" in text:
            return "KEY_OR_PERMISSION"
        if "TIMEOUT" in text or "CONNECTION" in text or "503" in text or "UNAVAILABLE" in text:
            return "TEMPORARY_SERVICE_ERROR"
        return "OTHER_ERROR"

    async def _request(self, key: str, prompt: str, system_instruction: str) -> str:
        def call() -> str:
            client = genai.Client(api_key=key)
            config = {"response_mime_type": "application/json"}
            if system_instruction:
                config["system_instruction"] = system_instruction
            response = client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
            return (response.text or "").strip()

        return await asyncio.to_thread(call)

    def _ready_order(self) -> list[GeminiKeyState]:
        if not self.keys:
            return []
        ordered = self.keys[self.active_index :] + self.keys[: self.active_index]
        return [key for key in ordered if key.ready]

    def _sync_metrics(self) -> None:
        self.state.gemini_usage.update({
            "configured_keys": len(self.keys),
            "active_key_index": self.keys[self.active_index].index if self.keys else None,
            "requests_total": sum(item.requests for item in self.keys),
            "success_total": sum(item.successes for item in self.keys),
            "failures_total": sum(item.failures for item in self.keys),
            "keys": [
                {
                    "account_index": item.index,
                    "masked": item.masked,
                    "configured": True,
                    "requests": item.requests,
                    "successes": item.successes,
                    "failures": item.failures,
                    "ready": item.ready,
                    "last_error": item.last_error,
                    "last_error_category": item.last_error_category,
                    "diagnostic_requests": item.diagnostic_requests,
                    "diagnostic_successes": item.diagnostic_successes,
                    "diagnostic_failures": item.diagnostic_failures,
                    "diagnostic_checked_at": item.diagnostic_checked_at,
                }
                for item in self.keys
            ],
        })
