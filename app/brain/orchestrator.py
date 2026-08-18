from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.brain.gemini import GeminiKeyPool
from app.config.settings import get_settings
from app.market.service import MarketService
from app.state import RuntimeState
from app.storage.store import StorageManager

logger = logging.getLogger(__name__)
UTC = timezone.utc

SYSTEM_INSTRUCTION = """You are Fekra Trading Brain, the primary analytical decision engine, operating in PAPER mode only. You are a team of market analysts and risk managers, not an execution engine. Produce a professional, evidence-based decision that can be audited later; do not seek a trade merely because price moved and do not predict the future.

Use only the supplied Binance live observation and historical candle context for 5m, 15m, 1h, 4h, and 1d, plus the supplied free-news and economic context when available. Before deciding, explicitly investigate: market structure, higher-timeframe trend, momentum, liquidity, volume, volatility, support and resistance, news, and data quality. Do not invent missing values, indicators, sources, or economic context. A single indicator or news item must never determine direction.

Perform the analysis in this order: identify supporting evidence; identify opposing evidence; create at least one plausible alternative hypothesis when the data allows; assess uncertainty; assess data quality and missing or conflicting inputs; verify Entry, Stop Loss, every Take Profit target, and risk/reward; then conduct a final challenge review. In the challenge review ask whether strong contrary evidence exists, whether a better alternative hypothesis exists, whether a higher timeframe was ignored, whether the news is material or noise, and whether the risk is worth taking. If data is incomplete, conflicting, stale, low quality, or confidence is not genuinely strong, choose WAIT.

Return only valid JSON with exactly these top-level keys: action, thesis, summary, evidence, counter_evidence, alternative_hypotheses, uncertainty, invalidating_context, trade_setup, factor_scores, scoring, final_review. The action must be exactly one of LONG, SHORT, WAIT. LONG and SHORT are analytical decisions only; they are never real orders. Use WAIT whenever the setup is not fully valid. For LONG or SHORT, trade_setup must include entry_price, stop_loss, take_profit_targets (an array containing all targets), reward_risk_ratio, and a reason for each level. A directional decision without valid Entry, Stop Loss, at least one Take Profit target, and a positive risk/reward is invalid and must be changed to WAIT.

factor_scores must give an independent 0-100 score for market_structure, trend, momentum, liquidity, volume, volatility, support_resistance, news, and data_quality. scoring must contain approval_score from 0 to 100 and contribution_pct whose numeric values sum to 100. News/news_sentiment contribution must never exceed 10; distribute the remaining 90 dynamically among the market and risk factors. Include clear evidence objects with type, summary, interpretation, source, and timestamp when available. Evidence must distinguish technical Binance candle evidence from news article evidence; news evidence must cite the original article URL, not the RSS feed URL. Include concrete uncertainty, risks, and invalidating_context. final_review must record the challenge-review conclusions. Do not include internal IDs, raw database objects, execution_request, or any real-order instruction."""


class BrainOrchestrator:
    def __init__(self, state: RuntimeState, gemini: GeminiKeyPool, storage: StorageManager, market: MarketService | None = None) -> None:
        self.state = state
        self.gemini = gemini
        self.storage = storage
        self.market = market
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
        related_news = sorted(
            (item for item in self.state.news if symbol in item.get("symbols", [])),
            key=lambda item: item.get("published_at") or item.get("retrieved_at") or "",
            reverse=True,
        )[:10]
        historical = await self.market.historical_context(symbol) if self.market is not None else {"ready": False, "timeframes": {}, "errors": ["historical provider unavailable"]}
        news_context = [self._news_for_prompt(item) for item in related_news]
        warmup_ready = bool(historical.get("ready")) and len(historical.get("timeframes", {})) >= 5
        if warmup_ready:
            prompt = json.dumps({
                "objective": f"Investigate current context for {symbol} and decide whether to act or wait in PAPER mode.",
                "market_observation": ticker,
                "historical_context": historical,
                "related_news": news_context,
                "constraints": [
                    "Do not invent missing data.",
                    "Use the multi-timeframe candle context before claiming market structure, momentum, liquidity, or volatility.",
                    "News is capped at 10% of contribution weights; distribute the remaining 90% dynamically.",
                    "Never use risk/reward weight or propose BUY/SELL_REDUCE without valid entry_price, stop_loss, and take_profit levels.",
                    "Treat news older than 72 hours as background context, not a fresh catalyst.",
                    "Cite the original article URL and publication timestamp for every news-based evidence item.",
                    "Challenge the preferred explanation.",
                    "A WAIT decision is valid.",
                    "No real execution is possible.",
                ],
            }, ensure_ascii=False)
            result = await self.gemini.analyze(prompt, SYSTEM_INSTRUCTION)
            decision = self._parse_decision(result)
            decision = self._attach_news_metadata(decision, related_news)
        else:
            error = "Historical warm-up incomplete; safe WAIT until 5m, 15m, 1h, 4h, and 1d candles are available."
            decision = {
                "action": "WAIT",
                "summary": error,
                "evidence": [],
                "counter_evidence": [],
                "alternative_hypotheses": [],
                "uncertainty": "high",
                "invalidating_context": historical.get("errors", []),
                "scoring": self._normalize_scoring({}),
            }
            result = {"ok": True, "model": "historical-warmup-guard", "account_index": None}
        cycle = {
            "id": str(uuid.uuid4()),
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "trigger_type": trigger_type,
            "trigger_symbol": symbol,
            "status": "COMPLETED" if result.get("ok") else "ERROR",
            "objective": f"Investigate {symbol}",
            "summary": decision.get("summary", result.get("error", "No result")),
            "final_decision": decision.get("public_action", decision.get("action", "WAIT")),
            "model": result.get("model", get_settings().gemini_model),
            "account_index": result.get("account_index"),
            "workflow": [
                "راقب السعر الحي من Binance",
                "حمّل شموع 5m و15m و1h و4h و1d قبل التحليل",
                f"راجع {len(related_news)} خبرًا مرتبطًا من RSS المجاني مع المصدر والعمر",
                "أرسل سياق السوق متعدد الأطر والأخبار إلى Gemini للتحليل" if warmup_ready else "أوقف التحليل عند نقص Historical Warm-up وأعد WAIT آمنًا",
                "تحقق من عقد القرار والأوزان وEntry/Stop/Target قبل قبول أي اتجاه",
                "لم يتم تنفيذ أي أمر حقيقي لأن الوضع PAPER",
            ],
            "inputs": {
                "market_symbol": symbol,
                "market_observation": ticker,
                "historical_ready": warmup_ready,
                "historical_timeframes": sorted(historical.get("timeframes", {}).keys()),
                "historical_context": historical,
                "news_count": len(related_news),
                "news_sources": sorted({item.get("source", "RSS") for item in related_news}),
                "news_items": news_context,
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
    def _attach_news_metadata(decision: dict[str, Any], news_items: list[dict[str, Any]]) -> dict[str, Any]:
        if not news_items:
            return decision
        searchable = []
        for item in news_items:
            text = " ".join(str(item.get(key) or "") for key in ("title", "summary")).lower()
            words = {word for word in re.findall(r"[a-z0-9]{3,}", text) if word not in {"the", "and", "for", "with", "from", "this", "that"}}
            searchable.append((item, words))
        for group in ("evidence", "counter_evidence"):
            enriched = []
            for evidence in decision.get(group, []):
                evidence_type = str(evidence.get("type") or "").lower()
                is_news = "news" in evidence_type or "خبر" in evidence_type or "sentiment" in evidence_type
                if not is_news:
                    evidence["source"] = "Binance historical candles"
                    evidence["source_name"] = "Binance"
                    evidence["source_url"] = ""
                    enriched.append(evidence)
                    continue
                haystack = " ".join(str(evidence.get(key) or "") for key in ("summary", "interpretation")).lower()
                words = set(re.findall(r"[a-z0-9]{3,}", haystack))
                best_item = None
                best_score = 0
                for item, item_words in searchable:
                    score = len(words & item_words)
                    if score > best_score:
                        best_item, best_score = item, score
                if best_item is not None and best_score >= 2:
                    evidence["source"] = best_item.get("url") or evidence.get("source", "")
                    evidence["source_name"] = best_item.get("source") or evidence.get("source_name", "")
                    evidence["source_url"] = best_item.get("url") or ""
                    evidence["timestamp"] = best_item.get("published_at") or best_item.get("retrieved_at") or evidence.get("timestamp", "")
                    evidence["age_hours"] = BrainOrchestrator._news_for_prompt(best_item).get("age_hours")
                enriched.append(evidence)
            decision[group] = enriched
        decision["alternative_hypotheses"] = [
            item for item in decision.get("alternative_hypotheses", [])
            if item.get("summary") and "بيان من سياق التحليل" not in item.get("summary", "")
        ]
        return decision

    @staticmethod
    def _news_for_prompt(item: dict[str, Any]) -> dict[str, Any]:
        published = item.get("published_at") or item.get("retrieved_at")
        age_hours = None
        try:
            timestamp = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
            age_hours = round(max(0.0, (datetime.now(UTC) - timestamp).total_seconds() / 3600), 2)
        except (TypeError, ValueError):
            pass
        return {
            "title": item.get("title"),
            "summary": item.get("summary"),
            "url": item.get("url"),
            "source": item.get("source"),
            "published_at": published,
            "age_hours": age_hours,
            "symbols": item.get("symbols", []),
        }

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
        summary = item.get("summary") or item.get("statement") or item.get("thesis") or item.get("hypothesis") or item.get("description") or item.get("label") or data.get("summary") or data.get("statement") or data.get("hypothesis") or data.get("description") or ""
        interpretation = item.get("interpretation") or item.get("reason") or item.get("explanation") or data.get("interpretation") or ""
        source = item.get("source") or item.get("url") or data.get("source") or data.get("url") or ""
        timestamp = item.get("timestamp") or item.get("published_at") or item.get("retrieved_at") or data.get("timestamp") or data.get("published_at") or ""
        return {
            "type": str(item.get("type") or item.get("evidence_type") or default_type),
            "summary": str(summary),
            "interpretation": str(interpretation),
            "source": str(source),
            "source_name": str(item.get("source_name") or data.get("source_name") or ""),
            "source_url": str(item.get("source_url") or data.get("source_url") or source if str(source).startswith("http") else ""),
            "timestamp": str(timestamp),
            "age_hours": item.get("age_hours") or data.get("age_hours"),
        }

    @staticmethod
    def _normalize_trade_setup(parsed: dict[str, Any]) -> dict[str, Any]:
        raw = parsed.get("trade_setup") if isinstance(parsed.get("trade_setup"), dict) else {}
        values: dict[str, float] = {}
        for key, value in {
            "entry_price": raw.get("entry_price", parsed.get("entry_price")),
            "stop_loss": raw.get("stop_loss", parsed.get("stop_loss")),
        }.items():
            try:
                if value is not None:
                    values[key] = float(value)
            except (TypeError, ValueError):
                pass
        raw_targets = raw.get("take_profit_targets") or raw.get("targets") or parsed.get("take_profit_targets")
        if not isinstance(raw_targets, list):
            raw_targets = [raw.get("take_profit", parsed.get("take_profit"))]
        targets: list[dict[str, Any]] = []
        for index, target in enumerate(raw_targets, start=1):
            reason = ""
            candidate = target
            if isinstance(target, dict):
                candidate = target.get("price", target.get("take_profit", target.get("value")))
                reason = str(target.get("reason") or target.get("rationale") or "")
            try:
                price = float(candidate)
                if price > 0:
                    targets.append({"target": index, "price": price, "reason": reason})
            except (TypeError, ValueError):
                continue
        action = str(parsed.get("action", "WAIT")).upper()
        long_action = action in {"BUY", "LONG"}
        short_action = action in {"SELL_REDUCE", "SHORT"}
        valid = all(key in values and values[key] > 0 for key in ("entry_price", "stop_loss")) and bool(targets)
        if valid and long_action:
            valid = values["stop_loss"] < values["entry_price"] and all(item["price"] > values["entry_price"] for item in targets)
        elif valid and short_action:
            valid = values["stop_loss"] > values["entry_price"] and all(item["price"] < values["entry_price"] for item in targets)
        elif action not in {"BUY", "SELL_REDUCE", "LONG", "SHORT"}:
            valid = False
        selected_target = max(targets, key=lambda item: item["price"]) if long_action and targets else min(targets, key=lambda item: item["price"]) if short_action and targets else None
        take_profit = selected_target["price"] if selected_target else None
        if valid and take_profit is not None:
            risk = abs(values["entry_price"] - values["stop_loss"])
            reward = abs(take_profit - values["entry_price"])
            valid = risk > 0 and reward > 0
            ratio = round(reward / risk, 4) if valid else None
        else:
            risk = reward = 0.0
            ratio = None
        return {
            "available": bool(valid),
            "entry_price": values.get("entry_price"),
            "stop_loss": values.get("stop_loss"),
            "take_profit": take_profit,
            "take_profit_targets": targets,
            "risk_distance": risk,
            "reward_distance": reward,
            "reward_risk_ratio": ratio,
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
            if normalized_label in {"news", "news_sentiment", "news_impact", "الأخبار"} or "news" in normalized_label or "خبر" in normalized_label or "أخبار" in normalized_label:
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
        trade_setup = BrainOrchestrator._normalize_trade_setup(parsed)
        if "risk/reward" in contributions and not trade_setup["available"]:
            contributions["risk/reward"] = 0.0
            contributions = {label: value for label, value in contributions.items() if value > 0}
            total = sum(contributions.values()) or 1.0
            contributions = {label: round(value * 100 / total, 2) for label, value in contributions.items()}
            delta = round(100.0 - sum(contributions.values()), 2)
            anchor = next((label for label in contributions if label != "news"), "market_structure")
            contributions[anchor] = round(contributions.get(anchor, 0.0) + delta, 2)
        factor_sources = parsed.get("factor_scores") if isinstance(parsed.get("factor_scores"), dict) else raw.get("factor_scores")
        factor_sources = factor_sources if isinstance(factor_sources, dict) else {}
        factor_aliases = {
            "market_structure": {"market_structure", "market structure", "structure"},
            "trend": {"trend", "direction"},
            "momentum": {"momentum"},
            "liquidity": {"liquidity"},
            "volume": {"volume", "volume_analysis", "volume analysis"},
            "volatility": {"volatility"},
            "support_resistance": {"support_resistance", "support/resistance", "support resistance"},
            "news": {"news", "news_sentiment", "news sentiment"},
            "data_quality": {"data_quality", "data quality", "quality"},
        }
        factor_scores: dict[str, float] = {}
        for canonical, aliases in factor_aliases.items():
            candidate = next((value for key, value in factor_sources.items() if str(key).strip().lower() in aliases), 0)
            try:
                factor_scores[canonical] = round(min(100.0, max(0.0, float(candidate))), 2)
            except (TypeError, ValueError):
                factor_scores[canonical] = 0.0
        normalized_factor_keys = {str(key).strip().lower() for key in factor_sources}
        factor_scores_complete = all(any(alias in normalized_factor_keys for alias in aliases) for aliases in factor_aliases.values())
        return {
            "approval_score": round(approval_score, 2),
            "factor_scores": factor_scores,
            "factor_scores_complete": factor_scores_complete,
            "contribution_pct": contributions,
            "news_cap_pct": 10,
            "news_contribution_pct": contributions.get("news", 0),
            "weighting_mode": "gemini_dynamic_90_plus_news_cap_10",
            "trade_setup": trade_setup,
            "risk_reward_available": trade_setup["available"],
        }

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any] | None:
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].lstrip()
        decoder = json.JSONDecoder()
        try:
            parsed, _ = decoder.raw_decode(cleaned)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
        for index, char in enumerate(cleaned):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(cleaned[index:])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
        return None

    @staticmethod
    def _parse_decision(result: dict[str, Any]) -> dict[str, Any]:
        if not result.get("ok"):
            return {"action": "WAIT", "public_action": "WAIT", "summary": result.get("error", "Gemini unavailable"), "uncertainty": "high", "trade_setup": BrainOrchestrator._normalize_trade_setup({}), "scoring": BrainOrchestrator._normalize_scoring({})}
        text = (result.get("text") or "").strip()
        parsed = BrainOrchestrator._extract_json_object(text)
        try:
            if parsed is None:
                raise json.JSONDecodeError("No JSON object found", text, 0)
            requested_action = str(parsed.get("action", "WAIT")).upper()
            action_map = {"LONG": "BUY", "SHORT": "SELL_REDUCE", "BUY": "BUY", "SELL_REDUCE": "SELL_REDUCE", "WAIT": "WAIT"}
            if requested_action not in action_map:
                parsed["action"] = "WAIT"
                parsed["public_action"] = "WAIT"
            else:
                parsed["action"] = action_map[requested_action]
                parsed["public_action"] = requested_action if requested_action in {"LONG", "SHORT", "WAIT"} else {"BUY": "LONG", "SELL_REDUCE": "SHORT"}[requested_action]
            for key in ("evidence", "counter_evidence", "alternative_hypotheses", "invalidating_context"):
                parsed[key] = [BrainOrchestrator._normalize_evidence_item(item, key) for item in BrainOrchestrator._as_list(parsed.get(key))]
            parsed["trade_setup"] = BrainOrchestrator._normalize_trade_setup(parsed)
            parsed["scoring"] = BrainOrchestrator._normalize_scoring(parsed)
            if parsed.get("action") in {"BUY", "SELL_REDUCE"} and not parsed["trade_setup"]["available"]:
                parsed["action"] = "WAIT"
                parsed["public_action"] = "WAIT"
                parsed["summary"] = "لا يوجد Entry/Stop Loss/Take Profit صالح؛ تم تحويل الاتجاه إلى WAIT آمن."
                parsed["uncertainty"] = "high"
            return parsed
        except json.JSONDecodeError:
            return {
                "action": "WAIT",
                "public_action": "WAIT",
                "summary": "Gemini returned a non-structured response; defaulting to WAIT. A structured JSON response is required for evidence and scoring.",
                "evidence": [],
                "counter_evidence": [],
                "alternative_hypotheses": [],
                "uncertainty": "high",
                "invalidating_context": [],
                "trade_setup": BrainOrchestrator._normalize_trade_setup({}),
                "scoring": BrainOrchestrator._normalize_scoring({}),
            }
