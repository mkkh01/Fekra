from dataclasses import dataclass


@dataclass(frozen=True)
class AssetProfile:
    key: str
    label: str
    confidence_delta: int
    minimum_rr: float
    atr_multiplier: float
    price_buffer: float
    min_relative_volume: float
    rsi_long_min: float
    rsi_long_max: float
    rsi_short_min: float
    rsi_short_max: float
    low_volatility: float
    high_volatility: float
    swing_lookback: int
    liquidity_note: str


PROFILES = {
    "major": AssetProfile(
        key="major", label="أصل رئيسي", confidence_delta=0, minimum_rr=2.0,
        atr_multiplier=1.25, price_buffer=0.002, min_relative_volume=0.90,
        rsi_long_min=53, rsi_long_max=77, rsi_short_min=23, rsi_short_max=47,
        low_volatility=0.0035, high_volatility=0.030, swing_lookback=3,
        liquidity_note="سيولة مرتفعة؛ تم تشديد تأكيد الحجم قليلًا",
    ),
    "large_cap": AssetProfile(
        key="large_cap", label="عملة كبيرة", confidence_delta=2, minimum_rr=2.0,
        atr_multiplier=1.30, price_buffer=0.0025, min_relative_volume=0.95,
        rsi_long_min=53, rsi_long_max=77, rsi_short_min=23, rsi_short_max=47,
        low_volatility=0.0040, high_volatility=0.035, swing_lookback=3,
        liquidity_note="سيولة جيدة؛ يتطلب الحجم تأكيدًا أعلى من المتوسط",
    ),
    "mid_cap": AssetProfile(
        key="mid_cap", label="عملة متوسطة", confidence_delta=4, minimum_rr=2.1,
        atr_multiplier=1.45, price_buffer=0.003, min_relative_volume=1.00,
        rsi_long_min=54, rsi_long_max=76, rsi_short_min=24, rsi_short_max=46,
        low_volatility=0.0045, high_volatility=0.040, swing_lookback=4,
        liquidity_note="سيولة متوسطة؛ تم رفع فلتر الحجم وتقليل الضوضاء",
    ),
    "volatile": AssetProfile(
        key="volatile", label="عالية التذبذب", confidence_delta=7, minimum_rr=2.2,
        atr_multiplier=1.70, price_buffer=0.004, min_relative_volume=1.10,
        rsi_long_min=55, rsi_long_max=75, rsi_short_min=25, rsi_short_max=45,
        low_volatility=0.0050, high_volatility=0.055, swing_lookback=4,
        liquidity_note="تذبذب مرتفع؛ تم رفع الثقة المطلوبة وتوسيع الوقف",
    ),
}

MAJOR = {"BTCUSDT", "ETHUSDT"}
LARGE_CAP = {"BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "TRXUSDT", "LTCUSDT", "BCHUSDT"}
VOLATILE = {"DOGEUSDT", "OPUSDT", "ARBUSDT", "SEIUSDT", "VETUSDT", "ALGOUSDT", "RENDERUSDT"}


def profile_for(symbol: str, candles: list[dict[str, float]] | None = None) -> AssetProfile:
    symbol = symbol.upper()
    if symbol in MAJOR:
        base = PROFILES["major"]
    elif symbol in VOLATILE:
        base = PROFILES["volatile"]
    elif symbol in LARGE_CAP:
        base = PROFILES["large_cap"]
    else:
        base = PROFILES["mid_cap"]

    # A profile is a starting prior, not a permanent label. Promote an asset
    # when its recent realized range confirms materially higher volatility.
    if candles and len(candles) >= 20:
        closes = [float(c["close"]) for c in candles[-20:]]
        realized_range = (max(closes) - min(closes)) / max(closes[-1], 1e-9)
        if realized_range >= 0.28 and base.key != "volatile":
            return PROFILES["volatile"]
    return base
