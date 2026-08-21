from app.config import DEFAULT_SYMBOLS, Settings


def test_default_symbol_universe_is_ten_symbols():
    expected = {
        "BTCUSDT", "ETHUSDT", "LTCUSDT", "XRPUSDT", "ADAUSDT",
        "SOLUSDT", "DOTUSDT", "LINKUSDT", "AVAXUSDT", "BNBUSDT",
    }
    assert set(DEFAULT_SYMBOLS) == expected
    assert len(DEFAULT_SYMBOLS) == 10
    assert Settings().symbol_list == DEFAULT_SYMBOLS
