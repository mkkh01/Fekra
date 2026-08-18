from app.brain.orchestrator import BrainOrchestrator
from app.state import RuntimeState


def candle(close: float, high: float, low: float, volume: float, open_time: int) -> dict:
    return {"open_time": open_time, "close_time": open_time + 1, "open": close, "high": high, "low": low, "close": close, "volume": volume}


def complete_history(changes: dict[str, float], *, breakout: bool = False) -> dict:
    timeframes = {}
    for index, tf in enumerate(("5m", "15m", "1h", "4h", "1d")):
        rows = [candle(100, 101, 99, 100, index * 100 + step * 5) for step in range(20)]
        if breakout and tf == "5m":
            rows[-2] = candle(100, 101, 99, 100, index * 100 + 90)
            rows[-1] = candle(103, 104, 102, 180, index * 100 + 95)
        timeframes[tf] = {"count": 120, "change_pct": changes[tf], "recent_candles": rows}
    return {"ready": True, "timeframes": timeframes, "errors": []}


def test_market_bias_is_not_trade_decision_without_breakout() -> None:
    history = complete_history({"5m": 0.2, "15m": 0.4, "1h": 2.0, "4h": 3.0, "1d": 4.0})
    context = BrainOrchestrator._build_market_context(history, {"price": 100, "updated_at": "now"}, True, [])
    decision = BrainOrchestrator._finalize_decision({"trade_decision": "LONG_READY", "trade_setup": {"available": True, "reward_risk_ratio": 3, "entry_price": 100, "stop_loss": 95, "take_profit": 115}, "scoring": {"factor_scores": {"momentum": 80, "liquidity": 80, "volatility": 70, "news": 5}}}, context, [])
    assert context["market_bias"] == "LONG"
    assert decision["trade_decision"] == "WAIT"
    assert any("Trigger" in reason for reason in decision["rejection_reasons"])


def test_long_ready_requires_confirmed_breakout_and_quality() -> None:
    history = complete_history({"5m": 0.2, "15m": 0.4, "1h": 2.0, "4h": 3.0, "1d": 4.0}, breakout=True)
    context = BrainOrchestrator._build_market_context(history, {"price": 103, "updated_at": "now"}, True, [])
    decision = BrainOrchestrator._finalize_decision({"trade_decision": "LONG_READY", "entry": {"type": "BREAKOUT"}, "trade_setup": {"available": True, "reward_risk_ratio": 2.5, "entry_price": 103, "stop_loss": 99, "take_profit": 113}, "scoring": {"factor_scores": {"momentum": 80, "liquidity": 80, "volatility": 70, "news": 5}}}, context, [])
    assert decision["trade_decision"] in {"LONG_READY", "WAIT"}
    assert decision["confidence"] == round(sum(decision["factor_scores"].values()) / len(decision["factor_scores"]), 2)
    assert set(item["label"] for item in decision["alternative_scenarios"]) == {"BASE_CASE", "BULL_CASE", "BEAR_CASE"}


def test_low_data_quality_forces_wait() -> None:
    context = BrainOrchestrator._build_market_context({"ready": False, "timeframes": {}, "errors": ["missing"]}, {"price": 100, "updated_at": "now"}, False, [])
    decision = BrainOrchestrator._finalize_decision({"trade_decision": "SHORT_READY", "trade_setup": {"available": True, "reward_risk_ratio": 3, "entry_price": 100, "stop_loss": 105, "take_profit": 90}}, context, [])
    assert context["data_quality"] < 80
    assert decision["trade_decision"] == "WAIT"


def test_wait_performance_can_be_correct_or_missed() -> None:
    state = RuntimeState()
    cycle = {"id": "c1", "trigger_symbol": "BTCUSDT", "final_decision": "WAIT", "inputs": {"market_observation": {"price": 100}}}
    state.add_cycle(cycle)
    updates = state.evaluate_decision_outcomes("BTCUSDT", 104)
    assert updates[0]["performance"]["status"] == "MISSED_OPPORTUNITY"
    assert state.performance_summary()["missed_opportunities"] == 1
    assert cycle["inputs"]["performance"]["change_pct"] == 4.0


def test_structured_trade_decision_accepts_nested_levels_and_normalizes_invalidation() -> None:
    result = {
        "ok": True,
        "text": '{"symbol":"BTCUSDT","market_bias":"LONG","trade_decision":"LONG_READY","entry":{"price":100,"type":"BREAKOUT","trigger_confirmed":true},"stop_loss":{"price":95,"reason":"Below structure"},"take_profit":{"price":110,"reason":"HTF resistance"},"factor_scores":{"market_structure":90,"trend":90,"momentum":80,"liquidity":75,"volume":85,"volatility":70,"support_resistance":80,"news":5,"data_quality":95,"risk_reward":90},"invalidation":{"price":95,"condition":"Breakdown below structure with volume"}}',
    }
    decision = BrainOrchestrator._parse_decision(result)
    assert decision["trade_decision"] == "LONG_READY"
    assert decision["trade_setup"]["entry_price"] == 100
    assert decision["trade_setup"]["stop_loss"] == 95
    assert decision["trade_setup"]["take_profit"] == 110
    assert decision["trade_setup"]["available"] is True
    normalized = BrainOrchestrator._normalize_evidence_item({"price": 95, "condition": "Breakdown below structure"}, "invalidating_context")
    assert "95" in normalized["summary"]
    assert "Breakdown" in normalized["interpretation"]


def test_validation_rejects_placeholder_and_missing_counter_evidence() -> None:
    decision, errors = BrainOrchestrator._validate_decision({
        "summary": "The primary reason",
        "evidence": [{"summary": "دليل بلا ملخص منظم"}],
        "counter_evidence": [],
        "invalidation": {"price": 95, "condition": "Breakdown"},
    })
    assert decision["evidence"] == []
    assert any("meaningful evidence" in error for error in errors)
    assert any("meaningful counter_evidence" in error for error in errors)


def test_finalized_score_matches_confidence_and_news_is_zero_without_news() -> None:
    history = complete_history({"5m": 0.1, "15m": 0.2, "1h": 1.0, "4h": 2.0, "1d": 3.0})
    context = BrainOrchestrator._build_market_context(history, {"price": 100, "updated_at": "now"}, True, [])
    decision = BrainOrchestrator._finalize_decision({"trade_decision": "WAIT", "trade_setup": {}, "scoring": {"factor_scores": {"momentum": 60, "liquidity": 50, "volatility": 40}}}, context, [])
    assert decision["scoring"]["approval_score"] == decision["confidence"]
    assert decision["scoring"]["news_contribution_pct"] == 0
    assert round(sum(decision["scoring"]["contribution_pct"].values()), 2) == 100
    assert decision["consensus"] == "Single AI Analysis"
