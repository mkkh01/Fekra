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
    assert enriched["evidence"][0]["source_name"] == "example.com"
    assert enriched["alternative_hypotheses"] == []


def test_technical_evidence_keeps_binance_source() -> None:
    decision = {
        "evidence": [{"type": "Market Structure/Momentum", "summary": "Higher highs on 1h", "source": "https://cointelegraph.com/rss"}],
        "counter_evidence": [{"type": "Volume Analysis", "summary": "Volume confirms the move", "source": "https://cointelegraph.com/rss"}],
        "alternative_hypotheses": [],
    }
    enriched = BrainOrchestrator._attach_news_metadata(decision, [{
        "title": "Unrelated headline",
        "summary": "News context",
        "url": "https://example.com/article",
        "source": "Example News",
        "published_at": "2026-08-18T12:00:00+00:00",
    }])
    assert enriched["evidence"][0]["source"] == "Binance historical candles"
    assert enriched["evidence"][0]["source_name"] == "Binance"
    assert enriched["evidence"][0]["source_url"] == ""
    assert enriched["counter_evidence"][0]["source"] == "Binance historical candles"


def test_news_prompt_keeps_article_url_separate_from_feed_source() -> None:
    item = {
        "title": "Bitcoin article",
        "url": "https://example.com/article",
        "source": "https://example.com/rss",
        "published_at": "2026-08-18T12:00:00+00:00",
    }
    prompt_item = BrainOrchestrator._news_for_prompt(item)
    assert prompt_item["url"] == "https://example.com/article"
    assert prompt_item["source"] == "https://example.com/article"
    assert prompt_item["source_feed"] == "https://example.com/rss"
    assert prompt_item["article_url"] == "https://example.com/article"


def test_news_metadata_exposes_article_url() -> None:
    decision = {
        "evidence": [{"type": "News Sentiment", "summary": "ETF inflows rose strongly", "interpretation": "Bullish"}],
        "counter_evidence": [],
        "alternative_hypotheses": [],
    }
    news = [{
        "title": "Bitcoin ETF inflows rose strongly",
        "summary": "Institutional demand increased",
        "url": "https://example.com/article",
        "source": "https://example.com/rss",
        "published_at": "2026-08-18T12:00:00+00:00",
    }]
    enriched = BrainOrchestrator._attach_news_metadata(decision, news)
    assert enriched["evidence"][0]["source"] == "https://example.com/article"
    assert enriched["evidence"][0]["source_url"] == "https://example.com/article"
    assert enriched["evidence"][0]["source_name"] == "example.com"


def test_generic_evidence_matching_news_is_not_attributed_to_binance() -> None:
    decision = {
        "evidence": [{
            "type": "evidence",
            "summary": "News items: https://example.com/article highlights continued corporate adoption.",
            "interpretation": "Institutional adoption is supportive",
        }],
        "counter_evidence": [],
        "alternative_hypotheses": [],
    }
    news = [{
        "title": "Corporate adoption expands",
        "summary": "Institutional demand and adoption increased",
        "url": "https://example.com/article",
        "source": "https://example.com/rss",
        "published_at": "2026-08-18T12:00:00+00:00",
    }]
    enriched = BrainOrchestrator._attach_news_metadata(decision, news)
    item = enriched["evidence"][0]
    assert item["type"] == "news"
    assert item["source"] == "https://example.com/article"
    assert item["source_url"] == "https://example.com/article"
    assert item["source_name"] == "example.com"
