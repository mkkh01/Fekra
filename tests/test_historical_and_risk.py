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
