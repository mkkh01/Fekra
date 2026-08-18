from __future__ import annotations

import asyncio
import logging
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
                    key_state.cooldown_until = datetime.now(UTC) + timedelta(seconds=min(60 * (key_state.failures), 900))
                    self.state.gemini_usage["rotations_total"] += 1
                    self._sync_metrics()
                    self.state.event("GEMINI", "Gemini account failed; rotating to next account", {"account_index": key_state.index})
                    if position == len(ordered) - 1:
                        return {"ok": False, "error": "All configured Gemini accounts failed"}
        return {"ok": False, "error": "Gemini request failed"}

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
                }
                for item in self.keys
            ],
        })
