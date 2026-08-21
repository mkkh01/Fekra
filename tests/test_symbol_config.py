from app.config import DEFAULT_SYMBOLS, Settings


def test_default_symbol_universe_is_twenty_symbols():
    expected = {
        "BTCUSDT", "ETHUSDT", "LTCUSDT", "XRPUSDT", "ADAUSDT",
        "SOLUSDT", "DOTUSDT", "LINKUSDT", "AVAXUSDT", "BNBUSDT",
        "TRXUSDT", "BCHUSDT", "UNIUSDT", "ETCUSDT", "ATOMUSDT",
        "XLMUSDT", "NEARUSDT", "FILUSDT", "APTUSDT", "ARBUSDT",
    }
    assert set(DEFAULT_SYMBOLS) == expected
    assert len(DEFAULT_SYMBOLS) == 20
    assert Settings().symbol_list == DEFAULT_SYMBOLS
