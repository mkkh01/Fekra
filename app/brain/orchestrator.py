from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.brain.gemini import GeminiKeyPool
from app.config.settings import get_settings
from app.state import RuntimeState
from app.storage.store import StorageManager

logger = logging.getLogger(__name__)
UTC = timezone.utc

SYSTEM_INSTRUCTION = """You are Fekra Trading Brain in PAPER mode. You are a market research agent, not an execution engine. Distinguish observations from inferences, state uncertainty, propose alternatives, and prefer WAIT or NO_TRADE when evidence is insufficient. Never invent data or news. Return only valid JSON with keys: action, thesis, summary, evidence, counter_evidence, alternative_hypotheses, uncertainty, invalidating_context. Allowed actions: BUY, SELL_REDUCE, WAIT, NO_TRADE, MONITOR, CLOSE. Since this is PAPER mode, execution_request must not be included and no real order may be proposed."""


class BrainOrchestrator:
    def __init__(self, state: RuntimeState, gemini: GeminiKeyPool, storage: StorageManager) -> None:
        self.state = state
        self.gemini = gemini
        self.storage = storage
        self.task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._requested: asyncio.Queue[str] = asyncio.Queue()
        self.interval_seconds = 900
        self.killed = False

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run(), name="brain-orchestrator")

    async def stop(self) -> None:
        self._stop.set()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def request(self, symbol: str) -> None:
        if self.killed:
            return
        await self._requested.put(symbol.upper())

    def activate_kill_switch(self) -> None:
        self.killed = True
        self.state.kill_switch = True
        self.state.brain_status = "ERROR"

    async def _run(self) -> None:
        while not self._stop.is_set():
            if self.killed:
                await asyncio.sleep(5)
                continue
            try:
                symbol = await asyncio.wait_for(self._requested.get(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                symbol = self._pick_symbol()
            if symbol:
                await self.run_cycle(symbol, "manual" if not self._requested.empty() else "scheduled_review")

    def _pick_symbol(self) -> str:
        if not self.state.tickers:
            return "BTCUSDT"
        return max(self.state.tickers.values(), key=lambda x: abs(float(x.get("change_24h_pct", 0) or 0))).get("symbol", "BTCUSDT")

    async def run_cycle(self, symbol: str, trigger_type: str) -> dict[str, Any]:
        if self.killed:
            return {"status": "BLOCKED", "final_decision": "NO_TRADE"}
        self.state.brain_status = "RESEARCHING"
        started = datetime.now(UTC).isoformat()
        ticker = self.state.tickers.get(symbol, {"symbol": symbol, "status": "missing"})
        related_news = [item for item in self.state.news if symbol in item.get("symbols", [])][:10]
        prompt = json.dumps({
            "objective": f"Investigate current context for {symbol} and decide whether to act or wait in PAPER mode.",
            "market_observation": ticker,
            "related_news": related_news,
            "constraints": [
                "Do not invent missing data.",
                "Challenge the preferred explanation.",
                "A WAIT decision is valid.",
                "No real execution is possible.",
            ],
        }, ensure_ascii=False)
        result = await self.gemini.analyze(prompt, SYSTEM_INSTRUCTION)
        decision = self._parse_decision(result)
        cycle = {
            "id": str(uuid.uuid4()),
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "trigger_type": trigger_type,
            "trigger_symbol": symbol,
            "status": "COMPLETED" if result.get("ok") else "ERROR",
            "objective": f"Investigate {symbol}",
            "summary": decision.get("summary", result.get("error", "No result")),
            "final_decision": decision.get("action", "WAIT"),
            "model": result.get("model", get_settings().gemini_model),
            "account_index": result.get("account_index"),
            "workflow": [
                "راقب السعر الحي من Binance",
                f"راجع {len(related_news)} خبرًا مرتبطًا من RSS المجاني",
                "أرسل سياق السوق والأخبار إلى Gemini للتحليل",
                "تحقق من عقد القرار وأعد WAIT عند نقص البيانات أو فشل التحليل",
                "لم يتم تنفيذ أي أمر حقيقي لأن الوضع PAPER",
            ],
            "inputs": {
                "market_symbol": symbol,
                "market_observation": ticker,
                "news_count": len(related_news),
                "news_sources": sorted({item.get("source", "RSS") for item in related_news}),
                "data_timestamp": ticker.get("updated_at"),
            },
            "decision": decision,
        }
        self.state.add_cycle(cycle)
        self.state.event("DECISION" if result.get("ok") else "SYSTEM", f"{symbol}: {cycle['final_decision']}", {"cycle_id": cycle["id"]})
        await self.storage.write_cycle(cycle)
        await self.storage.write_event("DECISION" if result.get("ok") else "SYSTEM", cycle["summary"], {"cycle_id": cycle["id"], "symbol": symbol, "action": cycle["final_decision"]})
        self.state.brain_status = "MONITORING"
        return cycle

    @staticmethod
    def _parse_decision(result: dict[str, Any]) -> dict[str, Any]:
        if not result.get("ok"):
            return {"action": "WAIT", "summary": result.get("error", "Gemini unavailable"), "uncertainty": "high"}
        text = (result.get("text") or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        try:
            parsed = json.loads(text)
            action = parsed.get("action", "WAIT")
            if action not in {"BUY", "SELL_REDUCE", "WAIT", "NO_TRADE", "MONITOR", "CLOSE"}:
                parsed["action"] = "WAIT"
            return parsed
        except json.JSONDecodeError:
            return {
                "action": "WAIT",
                "summary": "Gemini returned a non-structured response; defaulting to WAIT.",
                "evidence": [],
                "counter_evidence": [],
                "alternative_hypotheses": [],
                "uncertainty": "high",
                "invalidating_context": [],
            }
