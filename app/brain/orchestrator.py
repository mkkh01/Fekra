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

SYSTEM_INSTRUCTION = """You are Fekra Trading Brain in PAPER mode. You are a market research agent, not an execution engine. Distinguish observations from inferences, state uncertainty, propose alternatives, and prefer WAIT or NO_TRADE when evidence is insufficient. Never invent data or news. Return only valid JSON with keys: action, thesis, summary, evidence, counter_evidence, alternative_hypotheses, uncertainty, invalidating_context, scoring. The scoring object must contain approval_score from 0 to 100 and contribution_pct, an object whose numeric values sum to 100. News/news_sentiment contribution must never exceed 10; distribute the remaining 90 dynamically among market structure, momentum, liquidity, volatility, risk/reward, data quality, or other relevant factors. Each evidence item should be an object with type, summary, interpretation, source, and timestamp when available; do not include internal IDs or raw database objects. Allowed actions: BUY, SELL_REDUCE, WAIT, NO_TRADE, MONITOR, CLOSE. Since this is PAPER mode, execution_request must not be included and no real order may be proposed."""


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
    def _as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    @staticmethod
    def _normalize_evidence_item(item: Any, default_type: str) -> dict[str, Any]:
        if isinstance(item, str):
            candidate = item.strip()
            if candidate.startswith("{"):
                try:
                    decoded = json.loads(candidate)
                    if isinstance(decoded, dict):
                        item = decoded
                except json.JSONDecodeError:
                    pass
            if isinstance(item, str):
                return {"type": default_type, "summary": candidate, "interpretation": "", "source": "", "timestamp": ""}
        if not isinstance(item, dict):
            return {"type": default_type, "summary": str(item), "interpretation": "", "source": "", "timestamp": ""}
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        summary = item.get("summary") or item.get("statement") or item.get("thesis") or data.get("summary") or "بيان من سياق التحليل"
        interpretation = item.get("interpretation") or item.get("reason") or item.get("explanation") or ""
        source = item.get("source") or item.get("url") or data.get("source") or data.get("url") or ""
        timestamp = item.get("timestamp") or item.get("published_at") or item.get("retrieved_at") or data.get("timestamp") or data.get("published_at") or ""
        return {
            "type": str(item.get("type") or item.get("evidence_type") or default_type),
            "summary": str(summary),
            "interpretation": str(interpretation),
            "source": str(source),
            "timestamp": str(timestamp),
        }

    @staticmethod
    def _normalize_scoring(parsed: dict[str, Any]) -> dict[str, Any]:
        raw = parsed.get("scoring") if isinstance(parsed.get("scoring"), dict) else {}
        raw_contributions = raw.get("contribution_pct") or raw.get("score_breakdown") or parsed.get("score_breakdown") or {}
        if isinstance(raw_contributions, list):
            raw_contributions = {str(item.get("factor", item.get("category", "factor"))): item.get("contribution_pct", item.get("value", 0)) for item in raw_contributions if isinstance(item, dict)}
        if not isinstance(raw_contributions, dict):
            raw_contributions = {}
        cleaned: dict[str, float] = {}
        news_value = 0.0
        for key, value in raw_contributions.items():
            try:
                number = max(0.0, float(value))
            except (TypeError, ValueError):
                continue
            label = str(key).strip() or "other"
            normalized_label = label.lower().replace(" ", "_")
            if normalized_label in {"news", "news_sentiment", "news_impact", "الأخبار"}:
                news_value += number
            else:
                cleaned[label] = cleaned.get(label, 0.0) + number
        news_value = min(news_value, 10.0)
        remaining = max(0.0, 100.0 - news_value)
        other_total = sum(cleaned.values())
        if other_total <= 0:
            cleaned = {"market_structure": remaining}
        else:
            cleaned = {label: value * remaining / other_total for label, value in cleaned.items()}
        contributions = {**cleaned, "news": news_value}
        contributions = {label: round(value, 2) for label, value in contributions.items() if value > 0}
        rounding_delta = round(100.0 - sum(contributions.values()), 2)
        if rounding_delta:
            anchor = next((label for label in contributions if label != "news"), "market_structure")
            contributions[anchor] = round(contributions.get(anchor, 0.0) + rounding_delta, 2)
        try:
            approval_score = min(100.0, max(0.0, float(raw.get("approval_score", parsed.get("approval_score", 0)))))
        except (TypeError, ValueError):
            approval_score = 0.0
        return {
            "approval_score": round(approval_score, 2),
            "contribution_pct": contributions,
            "news_cap_pct": 10,
            "news_contribution_pct": contributions.get("news", 0),
            "weighting_mode": "gemini_dynamic_90_plus_news_cap_10",
        }

    @staticmethod
    def _parse_decision(result: dict[str, Any]) -> dict[str, Any]:
        if not result.get("ok"):
            return {"action": "WAIT", "summary": result.get("error", "Gemini unavailable"), "uncertainty": "high", "scoring": BrainOrchestrator._normalize_scoring({})}
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
            for key in ("evidence", "counter_evidence", "alternative_hypotheses", "invalidating_context"):
                parsed[key] = [BrainOrchestrator._normalize_evidence_item(item, key) for item in BrainOrchestrator._as_list(parsed.get(key))]
            parsed["scoring"] = BrainOrchestrator._normalize_scoring(parsed)
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
                "scoring": BrainOrchestrator._normalize_scoring({}),
            }
