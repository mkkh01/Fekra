from app.brain.orchestrator import BrainOrchestrator


def test_news_alias_is_capped_and_reported_consistently() -> None:
    normalized = BrainOrchestrator._normalize_scoring({
        "scoring": {"approval_score": 75, "contribution_pct": {"News/news sentiment": 40, "Market Structure": 60}}
    })
    assert normalized["news_contribution_pct"] == 10
    assert normalized["contribution_pct"]["news"] == 10
    assert sum(normalized["contribution_pct"].values()) == 100


def test_attach_news_metadata_adds_article_url_and_age() -> None:
    decision = {
        "evidence": [{"type": "news", "summary": "ETF inflows rose strongly", "interpretation": "Bullish"}],
        "counter_evidence": [],
        "alternative_hypotheses": [{"type": "alternative_hypotheses", "summary": "بيان من سياق التحليل", "interpretation": ""}],
    }
    news = [{
        "title": "Bitcoin ETF inflows rose strongly",
        "summary": "Institutional demand increased",
        "url": "https://example.com/article",
        "source": "Example News",
        "published_at": "2026-08-18T12:00:00+00:00",
    }]
    enriched = BrainOrchestrator._attach_news_metadata(decision, news)
    assert enriched["evidence"][0]["source"] == "https://example.com/article"
    assert enriched["evidence"][0]["source_name"] == "Example News"
    assert enriched["alternative_hypotheses"] == []
