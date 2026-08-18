from app.brain.orchestrator import BrainOrchestrator


def test_normalize_evidence_removes_internal_fields() -> None:
    item = {
        "id": "internal-id",
        "type": "market_observation",
        "summary": "Bitcoin gained 1%",
        "interpretation": "Modest positive movement",
        "data": {"price": 64000, "change_24h_pct": 1.0},
        "source": "https://example.com/source",
        "timestamp": "2026-08-18T12:00:00Z",
    }
    normalized = BrainOrchestrator._normalize_evidence_item(item, "evidence")

    assert normalized == {
        "type": "market_observation",
        "summary": "Bitcoin gained 1%",
        "interpretation": "Modest positive movement",
        "source": "https://example.com/source",
        "source_name": "",
        "source_url": "https://example.com/source",
        "timestamp": "2026-08-18T12:00:00Z",
        "age_hours": None,
    }
    assert "id" not in normalized
    assert "data" not in normalized


def test_normalize_json_string_evidence() -> None:
    raw = '{"type":"news","summary":"ETF outflows rose","interpretation":"Bearish pressure"}'
    normalized = BrainOrchestrator._normalize_evidence_item(raw, "evidence")
    assert normalized["type"] == "news"
    assert normalized["summary"] == "ETF outflows rose"
    assert normalized["interpretation"] == "Bearish pressure"
