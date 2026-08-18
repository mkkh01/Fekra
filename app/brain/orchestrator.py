from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from urllib.parse import urlparse
from datetime import datetime, timezone
from typing import Any

from app.brain.gemini import GeminiKeyPool
from app.config.settings import get_settings
from app.market.service import MarketService
from app.state import RuntimeState
from app.storage.store import StorageManager

logger = logging.getLogger(__name__)
UTC = timezone.utc

SYSTEM_INSTRUCTION = """You are Fekra Trading Brain, a PAPER-ONLY market research and risk committee. Never execute or propose a real order. The objective is to identify only high-quality opportunities, not to increase trade count. Separate MARKET BIAS from TRADE DECISION: market_bias must be LONG, SHORT, or NEUTRAL; trade_decision must be LONG_READY, SHORT_READY, or WAIT. A directional bias is not a trade. LONG bias with unconfirmed resistance breakout must remain WAIT.

Use only supplied Binance live data and candle context for 5m, 15m, 1h, 4h, and 1d, in priority order 1d, 4h, 1h, 15m, 5m. Higher timeframes define context and direction; lower timeframes only time entry. Explicitly analyze market regime (TRENDING, RANGING, VOLATILE, BREAKOUT, ACCUMULATION, DISTRIBUTION, or UNCERTAIN), HTF alignment, market structure, BOS/breakout, momentum, volume versus its average, liquidity (previous/equal highs and lows, sweeps and pools), support/resistance, volatility, risk/reward, news and macro conflict, and data quality. Do not invent indicators, sources, economic context, or probabilities.

A LONG_READY decision requires appropriate HTF alignment, structure, valid BOS or breakout, momentum, volume confirmation, acceptable liquidity, support/resistance room, volatility, risk/reward, no strong news or macro contradiction, data quality at least 80, and a confirmed entry trigger. If price is under immediate resistance, do not enter. A breakout requires actual level break, candle close above/below the level, adequate volume, no strong rejection, no misleading liquidity sweep, and continuing momentum. A touch is not a breakout. Prefer Breakout then Retest then Confirmation when the retest offers better risk/reward, and compare breakout entry with retest entry. Apply the symmetric rules for SHORT_READY.

Reject any setup with risk/reward below 2.0, random or overly tight stops, unjustified targets, missing structural invalidation, unconfirmed trigger, data quality below 80, strong contradiction, or weak confidence. Stops must be beyond meaningful structure/support/resistance and targets must be based on resistance, liquidity, prior highs/lows, HTF levels, or market structure rather than chosen only to inflate reward/risk. News is an assisting factor capped at 10% except that genuinely material global events may be flagged HIGH_IMPACT, but news alone must never override technical structure.

First produce bullish/supporting evidence, bearish/opposing evidence, three scenarios BASE_CASE, BULL_CASE, and BEAR_CASE with evidence-grounded qualitative likelihoods (never fabricated probabilities), uncertainty, and data-quality assessment. Then run a contradiction review: identify the strongest reason against the preferred bias, including resistance, weak volume, divergence, liquidity trap, HTF resistance, macro risk, overextension, or failed breakout. If the contradiction is strong, downgrade or choose WAIT. Every analysis needs invalidation price and condition. Convert objects to structured JSON; never emit [object Object].

Return JSON only with these keys: symbol, market_bias, trade_decision, thesis, summary, confidence, data_quality, data_quality_breakdown, market_regime, entry, stop_loss, take_profit, risk_reward, trade_setup, factor_scores, scoring, bullish_arguments, bearish_arguments, evidence, counter_evidence, alternative_hypotheses, alternative_scenarios, invalidation, invalidating_context, rejection_reasons, trigger_status, news_assessments, final_review, consensus. trade_decision must be exactly LONG_READY, SHORT_READY, or WAIT. When WAIT, explain the rejection reason and required trigger. When directional, entry must include price, type, trigger_confirmed, and reason; stop_loss and take_profit must include price/level and reason; trade_setup must include all targets and reasons. factor_scores must independently score market_structure, trend, momentum, liquidity, volume, volatility, support_resistance, news, data_quality, and risk_reward from 0 to 100. confidence must be calculated from actual factor scores and risk/reward, not a feeling. News evidence must cite the original article URL, never the RSS feed URL. Include previous/current decision comparison when supplied. No internal IDs, raw database objects, execution_request, or real-order instruction."""


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
        market_context = self._build_market_context(historical, ticker, self.state.market_connected, related_news)
        news_assessments = self._assess_news(related_news)
        previous_cycle = self.state.cycles[0] if self.state.cycles else None
        warmup_ready = bool(historical.get("ready")) and len(historical.get("timeframes", {})) >= 5
        if warmup_ready:
            prompt = json.dumps({
                "objective": f"Investigate current context for {symbol} and decide whether to act or wait in PAPER mode.",
                "market_observation": ticker,
                "historical_context": historical,
                "related_news": news_context,
                "deterministic_market_guardrails": market_context,
                "previous_cycle": {
                    "final_decision": previous_cycle.get("final_decision"),
                    "summary": previous_cycle.get("summary"),
                    "market_bias": (previous_cycle.get("decision") or {}).get("market_bias"),
                } if previous_cycle else None,
                "constraints": [
                    "Do not invent missing data.",
                    "Use the multi-timeframe candle context before claiming market structure, momentum, liquidity, or volatility.",
                    "News is capped at 10% of contribution weights; distribute the remaining 90% dynamically.",
                    "Never produce LONG_READY or SHORT_READY without confirmed trigger, data quality >= 80, structural stop, logical target, and risk/reward >= 2.0.",
                    "Separate market_bias from trade_decision; a bias is not a trade.",
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
                "public_action": "WAIT",
                "trade_decision": "WAIT",
                "summary": error,
                "evidence": [],
                "counter_evidence": [],
                "alternative_hypotheses": [],
                "uncertainty": "high",
                "invalidating_context": historical.get("errors", []),
                "scoring": self._normalize_scoring({}),
            }
            result = {"ok": True, "model": "historical-warmup-guard", "account_index": None}
        decision, validation_errors = self._validate_decision(decision, require_invalidation=False)
        if validation_errors:
            decision["trade_decision"] = "WAIT"
            decision["action"] = "WAIT"
            decision["public_action"] = "WAIT"
            decision["rejection_reasons"] = list(decision.get("rejection_reasons") or []) + validation_errors
        decision = self._finalize_decision(decision, market_context, news_assessments, previous_cycle)
        decision, final_validation_errors = self._validate_decision(decision)
        all_validation_errors = validation_errors + final_validation_errors
        if all_validation_errors:
            decision["trade_decision"] = "WAIT"
            decision["action"] = "WAIT"
            decision["public_action"] = "WAIT"
            decision["rejection_reasons"] = list(dict.fromkeys(list(decision.get("rejection_reasons") or []) + all_validation_errors))
            decision["validation"] = {"valid": False, "errors": decision["rejection_reasons"]}
            decision["summary"] = "WAIT: تم رفض التحليل لأنه لم يجتز طبقة التحقق المطلوبة."
        else:
            decision["validation"] = {"valid": True, "errors": []}
        cycle = {
            "id": str(uuid.uuid4()),
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "trigger_type": trigger_type,
            "trigger_symbol": symbol,
            "status": "COMPLETED" if result.get("ok") else "ERROR",
            "objective": f"Investigate {symbol}",
            "summary": decision.get("summary", result.get("error", "No result")),
            "final_decision": decision.get("trade_decision", decision.get("public_action", decision.get("action", "WAIT"))),
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
                "market_context": market_context,
                "news_assessments": news_assessments,
                "previous_cycle": previous_cycle.get("id") if previous_cycle else None,
                "decision_history": decision.get("decision_history", {}),
            },
            "decision": decision,
        }
        evaluated_cycles = self.state.evaluate_decision_outcomes(symbol, float(ticker.get("price", 0) or 0))
        for evaluated_cycle in evaluated_cycles:
            await self.storage.update_cycle_performance(evaluated_cycle)
        self.state.add_cycle(cycle)
        self.state.event("DECISION" if result.get("ok") else "SYSTEM", f"{symbol}: {cycle['final_decision']}", {"cycle_id": cycle["id"]})
        await self.storage.write_cycle(cycle)
        await self.storage.write_event("DECISION" if result.get("ok") else "SYSTEM", cycle["summary"], {"cycle_id": cycle["id"], "symbol": symbol, "action": cycle["final_decision"]})
        self.state.brain_status = "MONITORING"
        return cycle

    @staticmethod
    def _article_source_name(url: Any, fallback: str = "") -> str:
        try:
            host = urlparse(str(url or "")).netloc.lower().removeprefix("www.")
            if host:
                return host
        except ValueError:
            pass
        return fallback or "مصدر الخبر"

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
                summary_text = " ".join(str(evidence.get(key) or "") for key in ("summary", "interpretation")).lower()
                explicit_urls = set(re.findall(r"https?://[^\s)]+", summary_text))
                evidence_url = str(evidence.get("source_url") or evidence.get("source") or "")
                words = set(re.findall(r"[a-z0-9]{3,}", summary_text))
                best_item = None
                best_score = 0
                for item, item_words in searchable:
                    article_url = str(item.get("url") or "").lower()
                    score = len(words & item_words)
                    if article_url and any(article_url in url or url in article_url for url in explicit_urls):
                        score += 100
                    if article_url and article_url == evidence_url.lower():
                        score += 100
                    if score > best_score:
                        best_item, best_score = item, score
                is_news = (
                    "news" in evidence_type or "خبر" in evidence_type or "sentiment" in evidence_type
                    or best_item is not None and best_score >= 100
                )
                if is_news and best_item is not None and (best_score >= 1):
                    article_url = best_item.get("url") or evidence.get("source_url") or evidence.get("source", "")
                    evidence["type"] = "news"
                    evidence["source"] = article_url
                    evidence["source_name"] = BrainOrchestrator._article_source_name(article_url, "مصدر الخبر")
                    evidence["source_url"] = article_url
                    evidence["timestamp"] = best_item.get("published_at") or best_item.get("retrieved_at") or evidence.get("timestamp", "")
                    evidence["age_hours"] = BrainOrchestrator._news_for_prompt(best_item).get("age_hours")
                else:
                    evidence["type"] = evidence.get("type") or "technical_evidence"
                    evidence["source"] = "Binance historical candles"
                    evidence["source_name"] = "Binance"
                    evidence["source_url"] = ""
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
            "article_url": item.get("url"),
            "source": item.get("url") or item.get("source"),
            "source_feed": item.get("source"),
            "source_name": BrainOrchestrator._article_source_name(item.get("url"), item.get("source_name", "")),
            "published_at": published,
            "age_hours": age_hours,
            "symbols": item.get("symbols", []),
        }

    @staticmethod
    def _build_market_context(historical: dict[str, Any], ticker: dict[str, Any], market_connected: bool, related_news: list[dict[str, Any]]) -> dict[str, Any]:
        timeframes = historical.get("timeframes", {}) if isinstance(historical.get("timeframes"), dict) else {}
        priority = ["1d", "4h", "1h", "15m", "5m"]
        changes = {tf: float((timeframes.get(tf) or {}).get("change_pct", 0) or 0) for tf in priority}
        htf_values = [changes[tf] for tf in ("1d", "4h", "1h") if tf in timeframes]
        positive = sum(value > 0 for value in htf_values)
        negative = sum(value < 0 for value in htf_values)
        if len(htf_values) >= 2 and positive >= 2 and positive > negative:
            bias = "LONG"
        elif len(htf_values) >= 2 and negative >= 2 and negative > positive:
            bias = "SHORT"
        else:
            bias = "NEUTRAL"
        aligned = len(htf_values) >= 2 and (positive == len(htf_values) or negative == len(htf_values))
        latest = (timeframes.get("5m") or {}).get("recent_candles") or []
        one_hour = (timeframes.get("1h") or {}).get("recent_candles") or []
        reference = one_hour[-20:] or latest[-20:]
        current_price = float(ticker.get("price", 0) or 0)
        resistance = max((float(row.get("high", 0) or 0) for row in reference), default=0.0)
        support = min((float(row.get("low", 0) or 0) for row in reference), default=0.0)
        prior = reference[:-1]
        prior_high = max((float(row.get("high", 0) or 0) for row in prior), default=resistance)
        prior_low = min((float(row.get("low", 0) or 0) for row in prior), default=support)
        last_candle = latest[-1] if latest else (reference[-1] if reference else {})
        last_close = float(last_candle.get("close", current_price) or current_price)
        last_open = float(last_candle.get("open", last_close) or last_close)
        last_high = float(last_candle.get("high", last_close) or last_close)
        last_low = float(last_candle.get("low", last_close) or last_close)
        last_volume = float(last_candle.get("volume", 0) or 0)
        avg_volume = sum(float(row.get("volume", 0) or 0) for row in latest[:-1]) / max(1, len(latest[:-1])) if latest else 0.0
        volume_ratio = round(last_volume / avg_volume, 4) if avg_volume > 0 else 0.0
        candle_range = max(0.0, last_high - last_low)
        body = abs(last_close - last_open)
        upper_wick = max(0.0, last_high - max(last_open, last_close))
        lower_wick = max(0.0, min(last_open, last_close) - last_low)
        long_rejection = bool(candle_range and upper_wick > max(body * 1.5, candle_range * 0.4))
        short_rejection = bool(candle_range and lower_wick > max(body * 1.5, candle_range * 0.4))
        long_sweep = bool(prior_high and last_high > prior_high and last_close <= prior_high)
        short_sweep = bool(prior_low and last_low < prior_low and last_close >= prior_low)
        recent_closes = [float(row.get("close", 0) or 0) for row in latest[-4:]]
        momentum_long = len(recent_closes) >= 3 and recent_closes[-1] > recent_closes[-2] > recent_closes[-3]
        momentum_short = len(recent_closes) >= 3 and recent_closes[-1] < recent_closes[-2] < recent_closes[-3]
        long_breakout = bool(prior_high and last_close > prior_high and volume_ratio >= 1.1 and not long_rejection and not long_sweep and momentum_long)
        short_breakout = bool(prior_low and last_close < prior_low and volume_ratio >= 1.1 and not short_rejection and not short_sweep and momentum_short)
        near_resistance = bool(resistance and current_price <= resistance and resistance > 0 and (resistance - current_price) / resistance <= 0.01)
        near_support = bool(support and current_price >= support and support > 0 and (current_price - support) / support <= 0.01)
        trigger_confirmed = long_breakout or short_breakout
        trigger_direction = "LONG" if long_breakout else "SHORT" if short_breakout else "NONE"
        spread = ((resistance - support) / current_price * 100) if current_price and resistance > support else 0.0
        if trigger_confirmed:
            regime = "BREAKOUT"
        elif spread < 2.0 and len(htf_values) >= 2:
            regime = "RANGING"
        elif max(abs(value) for value in htf_values or [0]) > 8:
            regime = "VOLATILE"
        elif aligned:
            regime = "TRENDING"
        else:
            regime = "UNCERTAIN"
        continuity = 100.0
        missing = 100.0
        volume_integrity = 100.0
        for tf, compact in timeframes.items():
            rows = compact.get("recent_candles") or []
            if not rows or compact.get("count", 0) < 10:
                continuity = min(continuity, 40.0)
                missing = min(missing, 40.0)
            if any(float(row.get("volume", 0) or 0) < 0 for row in rows):
                volume_integrity = 0.0
            if len(rows) >= 3:
                intervals = [int(rows[i + 1].get("open_time", 0)) - int(rows[i].get("open_time", 0)) for i in range(len(rows) - 1)]
                median_interval = sorted(intervals)[len(intervals) // 2] if intervals else 0
                if median_interval and any(interval > median_interval * 2 for interval in intervals):
                    continuity = min(continuity, 70.0)
                    missing = min(missing, 60.0)
        ws_health = 100.0 if market_connected and ticker.get("updated_at") else 50.0
        completeness = 100.0 if all(tf in timeframes for tf in priority) else 50.0
        news_freshness = 90.0 if not related_news else max(0.0, min(100.0, 100.0 - max(float(BrainOrchestrator._news_for_prompt(item).get("age_hours") or 0) for item in related_news) / 72.0 * 20.0))
        quality_components = {
            "websocket_health": ws_health,
            "candle_continuity": continuity,
            "missing_candles": missing,
            "latency": ws_health,
            "volume_integrity": volume_integrity,
            "indicator_completeness": completeness,
            "news_freshness": round(news_freshness, 2),
        }
        data_quality = round(sum(quality_components.values()) / len(quality_components), 2)
        return {
            "market_bias": bias,
            "htf_alignment": aligned,
            "timeframe_changes": changes,
            "market_regime": regime,
            "levels": {"resistance": resistance, "support": support, "previous_high": prior_high, "previous_low": prior_low},
            "volume_ratio": volume_ratio,
            "breakout": {"confirmed": trigger_confirmed, "direction": trigger_direction, "close_confirmed": bool(last_close > prior_high or last_close < prior_low), "volume_confirmed": volume_ratio >= 1.1, "rejection_checked": not (long_rejection or short_rejection), "liquidity_sweep_checked": not (long_sweep or short_sweep), "momentum_confirmed": momentum_long or momentum_short, "rejection": "LONG_REJECTION" if long_rejection else "SHORT_REJECTION" if short_rejection else None, "liquidity_sweep": "LONG_SWEEP" if long_sweep else "SHORT_SWEEP" if short_sweep else None},
            "trigger_status": {"type": "BREAKOUT_CONFIRMATION" if trigger_direction != "NONE" else "BREAKOUT_OR_RETEST_CONFIRMATION", "confirmed": trigger_confirmed, "direction": trigger_direction, "reason": "إغلاق خارج المستوى مع حجم أعلى من المتوسط" if trigger_confirmed else "لم يثبت الإغلاق خارج المستوى مع تأكيد الحجم"},
            "entry_comparison": {"breakout_entry": current_price, "retest_entry": prior_high if bias == "LONG" else prior_low, "preferred": "RETEST_IF_CONFIRMED" if not trigger_confirmed else "COMPARE_RR_BEFORE_ENTRY"},
            "data_quality": data_quality,
            "data_quality_breakdown": quality_components,
            "liquidity": {"previous_high": prior_high, "previous_low": prior_low, "near_resistance": near_resistance, "near_support": near_support, "sweep_checked": True},
        }

    @staticmethod
    def _assess_news(news_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        high_impact_terms = ("federal reserve", "fed", "fomc", "war", "trump", "sec", "hack", "sanction", "etf approval", "rate decision")
        medium_terms = ("etf", "regulation", "institutional", "liquidation", "upgrade", "listing")
        assessments = []
        for item in news_items:
            text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
            impact = "HIGH_IMPACT" if any(term in text for term in high_impact_terms) else "MEDIUM_IMPACT" if any(term in text for term in medium_terms) else "LOW_IMPACT"
            age = BrainOrchestrator._news_for_prompt(item).get("age_hours")
            freshness = "FRESH" if age is not None and age <= 24 else "RECENT" if age is not None and age <= 72 else "STALE"
            source = str(item.get("source") or "")
            credibility = "KNOWN_RSS_SOURCE" if any(domain in source for domain in ("coindesk", "cointelegraph", "decrypt", "cryptobriefing", "ccn")) else "UNVERIFIED_SOURCE"
            assessments.append({"title": item.get("title"), "article_url": item.get("url"), "impact": impact, "freshness": freshness, "source_credibility": credibility, "market_relevance": "SYMBOL_MATCH", "expected_impact": "ASSISTING_ONLY"})
        return assessments

    @staticmethod
    def _text_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (int, float, bool)):
            return str(value)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _flatten_evidence_items(items: Any) -> list[Any]:
        if items is None or items == "":
            return []
        values = items if isinstance(items, list) else [items]
        flattened: list[Any] = []
        wrapper_keys = ("evidence", "counter_evidence", "bullish_arguments", "bearish_arguments", "arguments", "items", "points")
        for item in values:
            if isinstance(item, list):
                flattened.extend(BrainOrchestrator._flatten_evidence_items(item))
                continue
            if isinstance(item, dict):
                nested_key = next((key for key in wrapper_keys if key in item and item[key] not in (None, "")), None)
                meaningful_keys = {"summary", "statement", "thesis", "hypothesis", "description", "label", "reason", "source", "url", "type", "evidence_type", "price", "condition"}
                if nested_key and not (set(item) & meaningful_keys - {nested_key}):
                    flattened.extend(BrainOrchestrator._flatten_evidence_items(item[nested_key]))
                    continue
            flattened.append(item)
        return flattened

    @staticmethod
    def _clean_evidence_items(items: Any) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        values = BrainOrchestrator._flatten_evidence_items(items)
        for item in values:
            if isinstance(item, dict):
                normalized = BrainOrchestrator._normalize_evidence_item(item, "evidence")
                summary = BrainOrchestrator._text_value(normalized.get("summary"))
                if summary and summary.lower() not in {"null", "undefined", "object", "[object object]", "دليل بلا ملخص منظم", "غير محدد"}:
                    normalized["summary"] = summary
                    normalized["interpretation"] = BrainOrchestrator._text_value(normalized.get("interpretation"))
                    cleaned.append(normalized)
            else:
                summary = BrainOrchestrator._text_value(item)
                if summary and summary.lower() not in {"null", "undefined", "object", "[object object]", "دليل بلا ملخص منظم", "غير محدد"}:
                    cleaned.append({"type": "evidence", "summary": summary, "interpretation": "", "source": "", "source_name": "", "timestamp": ""})
        return cleaned

    @staticmethod
    def _text_list(value: Any) -> list[str]:
        values = value if isinstance(value, list) else [value] if value not in (None, "") else []
        if isinstance(value, list):
            compacted: list[Any] = []
            character_buffer: list[str] = []
            for raw in values:
                if isinstance(raw, str) and len(raw) <= 1:
                    character_buffer.append(raw)
                else:
                    if character_buffer:
                        compacted.append("".join(character_buffer))
                        character_buffer = []
                    compacted.append(raw)
            if character_buffer:
                compacted.append("".join(character_buffer))
            values = compacted
        result: list[str] = []
        for item in values:
            if isinstance(item, dict):
                item = item.get("summary") or item.get("reason") or item.get("description") or item.get("condition") or item
            text = BrainOrchestrator._text_value(item).strip()
            if text and text.lower() not in {"null", "undefined", "object", "[object object]", "غير محدد"}:
                result.append(text)
        return result

    @staticmethod
    def _validate_decision(decision: dict[str, Any], *, require_invalidation: bool = True) -> tuple[dict[str, Any], list[str]]:
        errors: list[str] = []
        placeholder_pattern = re.compile(r"(?:^|\s)(?:null|undefined|object|\[object object\]|دليل بلا ملخص منظم|غير محدد)(?:$|\s)", re.IGNORECASE)
        for key in ("summary", "thesis"):
            value = BrainOrchestrator._text_value(decision.get(key))
            if value and placeholder_pattern.search(value):
                errors.append(f"{key} contains a placeholder")
            elif value:
                decision[key] = value
        for key in ("evidence", "counter_evidence"):
            decision[key] = BrainOrchestrator._clean_evidence_items(decision.get(key))
            if not decision[key]:
                errors.append(f"missing meaningful {key}")
        decision["alternative_hypotheses"] = BrainOrchestrator._clean_evidence_items(decision.get("alternative_hypotheses"))
        invalidation = decision.get("invalidation") if isinstance(decision.get("invalidation"), dict) else {}
        if require_invalidation and (not invalidation.get("price") or not invalidation.get("condition")):
            errors.append("missing invalidation price or condition")
        for key in ("market_bias", "trade_decision", "market_regime"):
            if decision.get(key) is not None and not isinstance(decision.get(key), str):
                errors.append(f"{key} is not a string")
        return decision, errors

    @staticmethod
    def _finalize_decision(decision: dict[str, Any], market: dict[str, Any], news_assessments: list[dict[str, Any]], previous: dict[str, Any] | None = None) -> dict[str, Any]:
        bias = market.get("market_bias", "NEUTRAL")
        requested = str(decision.get("trade_decision") or ("LONG_READY" if decision.get("action") == "BUY" else "SHORT_READY" if decision.get("action") == "SELL_REDUCE" else "WAIT")).upper()
        if requested not in {"LONG_READY", "SHORT_READY", "WAIT"}:
            requested = "WAIT"
        setup = decision.get("trade_setup") if isinstance(decision.get("trade_setup"), dict) else {}
        rr = float(setup.get("reward_risk_ratio") or 0)
        model_trigger = decision.get("trigger_status") if isinstance(decision.get("trigger_status"), dict) else {}
        trigger_confirmed = bool(market.get("trigger_status", {}).get("confirmed")) and bool(model_trigger.get("confirmed", True))
        rejection: list[str] = BrainOrchestrator._text_list(decision.get("rejection_reasons"))
        contradiction_text = " ".join(json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item) for item in (decision.get("counter_evidence") or [])).lower()
        strong_contradiction_terms = ("resistance", "weak volume", "divergence", "liquidity trap", "failed breakout", "macro", "overextension", "رفض", "كسر", "سحب سيولة")
        strong_contradiction = any(term in contradiction_text for term in strong_contradiction_terms)
        if strong_contradiction and requested in {"LONG_READY", "SHORT_READY"}:
            rejection.append("تعارض قوي في الأدلة المضادة؛ تم خفض القرار إلى WAIT")
        if market.get("data_quality", 0) < 80:
            rejection.append(f"جودة البيانات {market.get('data_quality', 0):.1f} أقل من الحد الأدنى 80")
        if requested in {"LONG_READY", "SHORT_READY"}:
            expected_bias = "LONG" if requested == "LONG_READY" else "SHORT"
            if bias != expected_bias:
                rejection.append(f"انحياز السوق الحتمي هو {bias} وليس {expected_bias}")
            if not trigger_confirmed:
                rejection.append("لم يتأكد Trigger الاختراق/إعادة الاختبار بإغلاق وحجم مناسب")
            if rr < 2.0:
                rejection.append(f"نسبة المخاطرة إلى العائد {rr:.2f} أقل من 2.0")
            if not setup.get("available"):
                rejection.append("Entry/Stop/Target أو البنية السعرية للصفقة غير صالحة")
            if market.get("market_regime") == "RANGING" and str((decision.get("entry") or {}).get("type", "")).upper() == "BREAKOUT":
                rejection.append("السوق جانبي ولا توجد مصادقة Breakout كافية")
            if market.get("liquidity", {}).get("near_resistance" if expected_bias == "LONG" else "near_support") and not trigger_confirmed:
                rejection.append("السعر قريب من تجمع سيولة/مستوى محوري؛ الانتظار مطلوب قبل الدخول")
        trade_decision = requested if not rejection and requested != "WAIT" else "WAIT"
        internal_action = "BUY" if trade_decision == "LONG_READY" else "SELL_REDUCE" if trade_decision == "SHORT_READY" else "WAIT"
        if trade_decision == "WAIT" and not rejection:
            rejection.append("لا توجد مصادقة كاملة لفرصة قابلة للتنفيذ الآن")
        factor_scores = dict((decision.get("scoring") or {}).get("factor_scores") or decision.get("factor_scores") or {})
        factor_scores["data_quality"] = round(float(market.get("data_quality", 0) or 0), 2)
        factor_scores["trend"] = 80.0 if market.get("htf_alignment") else 45.0
        factor_scores["market_structure"] = 80.0 if market.get("breakout", {}).get("confirmed") else 45.0
        factor_scores["volume"] = round(min(100.0, max(0.0, float(market.get("volume_ratio", 0) or 0) * 70.0)), 2)
        factor_scores["support_resistance"] = 45.0 if market.get("liquidity", {}).get("near_resistance" if bias == "LONG" else "near_support") else 75.0
        factor_scores["risk_reward"] = round(min(100.0, max(0.0, rr / 2.0 * 100.0)), 2)
        if not news_assessments:
            factor_scores["news"] = 0.0
        normalized_scores = {}
        for key in ("market_structure", "trend", "momentum", "liquidity", "volume", "volatility", "support_resistance", "news", "data_quality", "risk_reward"):
            try:
                normalized_scores[key] = round(min(100.0, max(0.0, float(factor_scores.get(key, 0) or 0))), 2)
            except (TypeError, ValueError):
                normalized_scores[key] = 0.0
        confidence = round(sum(normalized_scores.values()) / len(normalized_scores), 2)
        news_weight = min(10.0, round(normalized_scores.get("news", 0.0) / 10.0, 2)) if news_assessments else 0.0
        weight_keys = [key for key in normalized_scores if key != "news"]
        weight_total = sum(normalized_scores[key] for key in weight_keys)
        remaining_weight = 100.0 - news_weight
        contributions = {key: round(normalized_scores[key] * remaining_weight / weight_total, 2) for key in weight_keys} if weight_total > 0 else {"market_structure": remaining_weight}
        contributions["news"] = news_weight
        contribution_delta = round(100.0 - sum(contributions.values()), 2)
        anchor = next((key for key in contributions if key != "news"), "market_structure")
        contributions[anchor] = round(contributions.get(anchor, 0.0) + contribution_delta, 2)
        invalidation = dict(decision.get("invalidation")) if isinstance(decision.get("invalidation"), dict) else {}
        derived_price = setup.get("stop_loss") or (market.get("levels", {}).get("support") if bias == "LONG" else market.get("levels", {}).get("resistance"))
        if not invalidation.get("price") and derived_price:
            invalidation["price"] = derived_price
        if not invalidation.get("condition"):
            invalidation["condition"] = "كسر البنية المقابلة مع حجم مؤكد؛ لا يُعتمد الاتجاه قبل تحقق Trigger صالح"
        if not invalidation.get("price"):
            invalidation["price"] = market.get("levels", {}).get("support") or market.get("levels", {}).get("resistance")
        scenarios = decision.get("alternative_scenarios") if isinstance(decision.get("alternative_scenarios"), list) else []
        if len(scenarios) < 3:
            scenarios = [
                {"label": "BASE_CASE", "scenario": "استمرار السياق الحالي مع انتظار Trigger المؤكد", "likelihood": "مشروط بالأدلة الحالية"},
                {"label": "BULL_CASE", "scenario": "اختراق/استعادة المستوى مع إغلاق وحجم واستمرار زخم", "likelihood": "يتطلب تأكيدًا إضافيًا"},
                {"label": "BEAR_CASE", "scenario": "رفض المستوى أو Sweep مضلل ثم كسر البنية", "likelihood": "خطر إبطال يجب مراقبته"},
            ]
        previous_decision = (previous or {}).get("final_decision") or (previous or {}).get("decision", {}).get("trade_decision")
        current_decision = trade_decision
        decision["action"] = internal_action
        decision["public_action"] = "LONG" if trade_decision == "LONG_READY" else "SHORT" if trade_decision == "SHORT_READY" else "WAIT"
        decision["trade_decision"] = trade_decision
        decision["market_bias"] = bias
        decision["market_regime"] = market.get("market_regime")
        uncertainty_score = round(max(0.0, min(100.0, 100.0 - confidence + (10.0 if strong_contradiction else 0.0))), 2)
        uncertainty_level = "high" if uncertainty_score >= 60 else "medium" if uncertainty_score >= 30 else "low"
        decision["confidence"] = confidence
        decision["scoring"] = {**(decision.get("scoring") or {}), "approval_score": confidence, "contribution_pct": contributions, "factor_scores": normalized_scores, "news_contribution_pct": news_weight, "news_cap_pct": 10, "weighting_mode": "deterministic_factor_scores"}
        decision["data_quality"] = market.get("data_quality", 0)
        decision["data_quality_breakdown"] = market.get("data_quality_breakdown", {})
        decision["factor_scores"] = normalized_scores
        decision["entry"] = decision.get("entry") if isinstance(decision.get("entry"), dict) else {"price": setup.get("entry_price"), "type": "BREAKOUT" if market.get("trigger_status", {}).get("type") == "BREAKOUT_CONFIRMATION" else "RETEST_OR_CONFIRMATION", "trigger_confirmed": trigger_confirmed, "reason": "مستوى صالح بعد تحقق شروط المصادقة" if trigger_confirmed else "ينتظر تحقق Trigger"}
        decision["stop_loss"] = decision.get("stop_loss") if isinstance(decision.get("stop_loss"), dict) else {"price": setup.get("stop_loss"), "reason": "خلف بنية السوق/منطقة الإبطال"}
        decision["take_profit"] = decision.get("take_profit") if isinstance(decision.get("take_profit"), dict) else {"price": setup.get("take_profit"), "reason": "مستوى سيولة/مقاومة أو دعم HTF"}
        decision["take_profit_targets"] = setup.get("take_profit_targets", [])
        decision["risk_reward"] = rr
        trigger_status = dict(market.get("trigger_status", {}))
        trigger_status["status"] = "TRIGGERED" if trigger_status.get("confirmed") else "WAITING"
        decision["trigger_status"] = trigger_status
        decision["news_assessments"] = news_assessments
        decision["alternative_scenarios"] = scenarios
        decision["invalidation"] = invalidation
        decision["rejection_reasons"] = rejection
        decision["uncertainty_score"] = uncertainty_score
        decision["uncertainty"] = uncertainty_level
        decision["uncertainty_reasons"] = BrainOrchestrator._text_list(rejection[:4]) or ["لا يوجد تعارض قوي؛ درجة عدم اليقين محسوبة من العوامل والقيود الحالية"]
        decision["consensus"] = "Single AI Analysis"
        decision["consensus_detail"] = {"votes": {"LONG": 1 if trade_decision == "LONG_READY" else 0, "WAIT": 1 if trade_decision == "WAIT" else 0, "SHORT": 1 if trade_decision == "SHORT_READY" else 0}, "winner": "LONG" if trade_decision == "LONG_READY" else "SHORT" if trade_decision == "SHORT_READY" else "WAIT", "percentage": 100.0, "analysis_count": 1}
        decision["final_review"] = {
            "strong_contrary_evidence": strong_contradiction,
            "better_alternative": len(scenarios) >= 3,
            "higher_timeframe_checked": all(tf in (market.get("timeframe_changes") or {}) for tf in ("1d", "4h", "1h")),
            "news_materiality_checked": True,
            "risk_worth_taking": trade_decision != "WAIT" and rr >= 2.0,
            "result": "WAIT" if trade_decision == "WAIT" else "ACCEPTED_FOR_PAPER_RESEARCH",
        }
        decision["decision_history"] = {"previous": previous_decision or "NONE", "current": current_decision, "what_changed": BrainOrchestrator._text_list(rejection) if current_decision == "WAIT" else ["تم اجتياز شروط المصادقة الحتمية"]}
        if trade_decision == "WAIT":
            decision["summary"] = f"WAIT: {rejection[0]}" if rejection else decision.get("summary", "WAIT")
        return decision

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
        structured_summary = ""
        if item.get("price") is not None or item.get("condition"):
            structured_summary = f"السعر: {item.get('price', '—')} · الشرط: {item.get('condition', '—')}"
        summary = item.get("summary") or item.get("statement") or item.get("thesis") or item.get("hypothesis") or item.get("description") or item.get("label") or item.get("evidence") or item.get("counter_evidence") or item.get("bullish_arguments") or item.get("bearish_arguments") or item.get("reason") or data.get("summary") or data.get("statement") or data.get("hypothesis") or data.get("description") or data.get("evidence") or structured_summary or ""
        interpretation = item.get("interpretation") or item.get("reason") or item.get("explanation") or data.get("interpretation") or item.get("condition") or ""
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
            "entry_price": raw.get("entry_price", parsed.get("entry_price", (parsed.get("entry") or {}).get("price") if isinstance(parsed.get("entry"), dict) else None)),
            "stop_loss": raw.get("stop_loss", parsed.get("stop_loss")),
        }.items():
            try:
                if isinstance(value, dict):
                    value = value.get("price", value.get("level", value.get("value")))
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
            if not parsed.get("evidence"):
                parsed["evidence"] = parsed.get("bullish_arguments") or []
            if not parsed.get("counter_evidence"):
                parsed["counter_evidence"] = parsed.get("bearish_arguments") or []
            parsed["evidence"] = BrainOrchestrator._clean_evidence_items(parsed.get("evidence"))
            parsed["counter_evidence"] = BrainOrchestrator._clean_evidence_items(parsed.get("counter_evidence"))
            requested_action = str(parsed.get("trade_decision") or parsed.get("action", "WAIT")).upper()
            action_map = {"LONG_READY": "BUY", "SHORT_READY": "SELL_REDUCE", "LONG": "BUY", "SHORT": "SELL_REDUCE", "BUY": "BUY", "SELL_REDUCE": "SELL_REDUCE", "WAIT": "WAIT"}
            if requested_action not in action_map:
                parsed["action"] = "WAIT"
                parsed["trade_decision"] = "WAIT"
                parsed["public_action"] = "WAIT"
            else:
                parsed["action"] = action_map[requested_action]
                parsed["trade_decision"] = requested_action if requested_action in {"LONG_READY", "SHORT_READY", "WAIT"} else "LONG_READY" if requested_action in {"LONG", "BUY"} else "SHORT_READY" if requested_action in {"SHORT", "SELL_REDUCE"} else "WAIT"
                parsed["public_action"] = "LONG" if parsed["trade_decision"] == "LONG_READY" else "SHORT" if parsed["trade_decision"] == "SHORT_READY" else "WAIT"
            for key in ("evidence", "counter_evidence", "alternative_hypotheses", "invalidating_context"):
                parsed[key] = [BrainOrchestrator._normalize_evidence_item(item, key) for item in BrainOrchestrator._as_list(parsed.get(key))]
            parsed["bullish_arguments"] = parsed["evidence"]
            parsed["bearish_arguments"] = parsed["counter_evidence"]
            parsed["trade_setup"] = BrainOrchestrator._normalize_trade_setup(parsed)
            parsed["scoring"] = BrainOrchestrator._normalize_scoring(parsed)
            if parsed.get("action") in {"BUY", "SELL_REDUCE"} and not parsed["trade_setup"]["available"]:
                parsed["action"] = "WAIT"
                parsed["trade_decision"] = "WAIT"
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
