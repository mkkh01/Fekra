from app.brain.orchestrator import BrainOrchestrator


def test_news_contribution_is_capped_at_ten_percent() -> None:
    decision = {
        "action": "WAIT",
        "scoring": {
            "approval_score": 62,
            "contribution_pct": {
                "news": 45,
                "market_structure": 25,
                "momentum": 30,
            },
        },
    }
    normalized = BrainOrchestrator._normalize_scoring(decision)
    contributions = normalized["contribution_pct"]
    assert normalized["news_contribution_pct"] == 10
    assert sum(contributions.values()) == 100
    assert contributions["market_structure"] + contributions["momentum"] == 90


def test_dynamic_weights_fill_remaining_ninety_when_news_missing() -> None:
    normalized = BrainOrchestrator._normalize_scoring({
        "scoring": {"approval_score": 75, "contribution_pct": {"market_structure": 40, "liquidity": 60}}
    })
    assert normalized["news_contribution_pct"] == 0
    assert sum(normalized["contribution_pct"].values()) == 100
    assert normalized["contribution_pct"]["market_structure"] == 40
    assert normalized["contribution_pct"]["liquidity"] == 60
