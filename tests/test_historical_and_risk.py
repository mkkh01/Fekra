import json

from app.brain.orchestrator import BrainOrchestrator
from app.market.service import MarketService


def test_compact_candles_preserves_multi_timeframe_statistics() -> None:
    rows = [
        {"open_time": 1, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 5, "close_time": 2},
        {"open_time": 3, "open": 11, "high": 14, "low": 10, "close": 13, "volume": 7, "close_time": 4},
    ]
    compact = MarketService._compact_candles(rows)
    assert compact["count"] == 2
    assert compact["high"] == 14
    assert compact["low"] == 9
    assert compact["change_pct"] == 18.1818
    assert len(compact["recent_candles"]) == 2


def test_directional_action_without_trade_levels_becomes_wait() -> None:
    result = {
        "ok": True,
        "text": json.dumps({
            "action": "BUY",
            "summary": "Buy without levels",
            "evidence": [],
            "counter_evidence": [],
            "alternative_hypotheses": [],
            "uncertainty": "medium",
            "invalidating_context": [],
            "scoring": {"approval_score": 80, "contribution_pct": {"market_structure": 90, "news": 10}},
        }),
    }
    decision = BrainOrchestrator._parse_decision(result)
    assert decision["action"] == "WAIT"
    assert decision["trade_setup"]["available"] is False
    assert decision["scoring"]["risk_reward_available"] is False


def test_buy_trade_setup_calculates_reward_risk() -> None:
    parsed = BrainOrchestrator._normalize_trade_setup({
        "action": "BUY",
        "trade_setup": {"entry_price": 100, "stop_loss": 95, "take_profit": 110},
    })
    assert parsed["available"] is True
    assert parsed["reward_risk_ratio"] == 2.0


def test_long_without_complete_levels_becomes_public_wait() -> None:
    result = {
        "ok": True,
        "text": json.dumps({
            "action": "LONG",
            "summary": "Long without complete levels",
            "evidence": [],
            "counter_evidence": [],
            "alternative_hypotheses": [],
            "uncertainty": "medium",
            "invalidating_context": [],
            "factor_scores": {"market_structure": 70, "trend": 60, "momentum": 55, "liquidity": 50, "volume": 52, "volatility": 45, "support_resistance": 58, "news": 20, "data_quality": 90},
            "scoring": {"approval_score": 65, "contribution_pct": {"market_structure": 90, "news": 10}},
        }),
    }
    decision = BrainOrchestrator._parse_decision(result)
    assert decision["action"] == "WAIT"
    assert decision["public_action"] == "WAIT"
    assert decision["scoring"]["factor_scores"]["data_quality"] == 90
    assert decision["scoring"]["factor_scores_complete"] is True


def test_short_with_multiple_targets_exposes_public_short() -> None:
    result = {
        "ok": True,
        "text": json.dumps({
            "action": "SHORT",
            "summary": "Short with complete levels",
            "evidence": [],
            "counter_evidence": [],
            "alternative_hypotheses": [{"type": "alternative_hypotheses", "summary": "A squeeze invalidates the short", "interpretation": "Higher timeframe resistance breaks"}],
            "uncertainty": "medium",
            "invalidating_context": [{"type": "invalidating_context", "summary": "Close above stop"}],
            "trade_setup": {"entry_price": 100, "stop_loss": 105, "take_profit_targets": [{"price": 95, "reason": "First support"}, {"price": 90, "reason": "Daily support"}]},
            "factor_scores": {"market_structure": 65, "trend": 60, "momentum": 62, "liquidity": 55, "volume": 58, "volatility": 48, "support_resistance": 70, "news": 10, "data_quality": 95},
            "scoring": {"approval_score": 72, "contribution_pct": {"market_structure": 70, "momentum": 20, "news": 10}},
        }),
    }
    decision = BrainOrchestrator._parse_decision(result)
    assert decision["action"] == "SELL_REDUCE"
    assert decision["public_action"] == "SHORT"
    assert decision["trade_setup"]["available"] is True
    assert len(decision["trade_setup"]["take_profit_targets"]) == 2
    assert decision["trade_setup"]["take_profit"] == 90


def test_system_instruction_requires_auditable_review() -> None:
    from app.brain.orchestrator import SYSTEM_INSTRUCTION

    for phrase in ("market_bias must be LONG, SHORT, or NEUTRAL", "counter_evidence", "factor_scores", "contradiction review", "original article URL", "PAPER-ONLY"):
        assert phrase in SYSTEM_INSTRUCTION
