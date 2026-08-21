from datetime import datetime, timezone
from typing import Any

from app.analysis.profiles import profile_for


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains, losses = [], []
    for a, b in zip(values[-period - 1:-1], values[-period:]):
        delta = b - a
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return 100 - (100 / (1 + avg_gain / avg_loss))


def _atr(candles: list[dict[str, float]], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    trs = []
    for prev, cur in zip(candles[:-1], candles[1:]):
        trs.append(max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]), abs(cur["low"] - prev["close"])))
    return sum(trs[-period:]) / min(period, len(trs))


def _swing_state(candles: list[dict[str, float]], lookback: int = 3) -> tuple[str, list[dict[str, Any]]]:
    if len(candles) < lookback * 2 + 3:
        return "NEUTRAL", []
    points = []
    for i in range(lookback, len(candles) - lookback):
        high = candles[i]["high"]
        low = candles[i]["low"]
        if high == max(c["high"] for c in candles[i - lookback:i + lookback + 1]):
            points.append({"index": i, "price": high, "kind": "high"})
        if low == min(c["low"] for c in candles[i - lookback:i + lookback + 1]):
            points.append({"index": i, "price": low, "kind": "low"})
    highs = [p["price"] for p in points if p["kind"] == "high"]
    lows = [p["price"] for p in points if p["kind"] == "low"]
    if len(highs) >= 2 and len(lows) >= 2:
        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            return "BULLISH", points[-8:]
        if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            return "BEARISH", points[-8:]
    return "NEUTRAL", points[-8:]


def _regime(closes: list[float], atr: float, low_volatility: float, high_volatility: float) -> str:
    if len(closes) < 30:
        return "TRANSITION"
    fast, slow = _ema(closes, 20)[-1], _ema(closes, 50)[-1]
    drift = (closes[-1] - closes[-20]) / max(closes[-20], 1e-9)
    vol = atr / max(closes[-1], 1e-9)
    if vol > high_volatility:
        return "HIGH_VOLATILITY"
    if vol < low_volatility:
        return "LOW_VOLATILITY"
    if fast > slow and drift > 0.01:
        return "TRENDING_UP"
    if fast < slow and drift < -0.01:
        return "TRENDING_DOWN"
    if abs(drift) < 0.008:
        return "RANGING"
    return "TRANSITION"


def _fmt(value: float) -> float:
    return round(float(value), 8)


def analyze(symbol: str, candles: list[dict[str, float]], interval: str = "15m", threshold: int = 65, minimum_rr: float = 2.0) -> dict[str, Any]:
    profile = profile_for(symbol, candles)
    effective_threshold = threshold + profile.confidence_delta
    effective_minimum_rr = max(minimum_rr, profile.minimum_rr)
    base = {
        "symbol": symbol,
        "interval": interval,
        "asset_profile": profile.key,
        "asset_profile_label": profile.label,
        "applied_threshold": effective_threshold,
        "applied_minimum_rr": effective_minimum_rr,
        "calibration_note": profile.liquidity_note,
        "ready": False,
    }
    if len(candles) < 30:
        return {        **base, "signal": "NO TRADE", "bias": "NEUTRAL", "confidence": 0, "reason": "بيانات غير كافية"}

    closes = [float(c["close"]) for c in candles]
    current = closes[-1]
    atr = _atr(candles)
    atr_pct = atr / max(current, 1e-9)
    rsi = _rsi(closes)
    structure, swings = _swing_state(candles, profile.swing_lookback)
    regime = _regime(closes, atr, profile.low_volatility, profile.high_volatility)
    ema20, ema50 = _ema(closes, 20)[-1], _ema(closes, 50)[-1]
    avg_vol = sum(float(c["volume"]) for c in candles[-20:]) / 20
    rel_vol = float(candles[-1]["volume"]) / max(avg_vol, 1e-9)

    direction = "LONG" if structure == "BULLISH" and ema20 >= ema50 else "SHORT" if structure == "BEARISH" and ema20 <= ema50 else "NEUTRAL"
    structure_score = 25 if structure != "NEUTRAL" else 10
    htf_score = 20 if direction != "NEUTRAL" and ((direction == "LONG" and regime == "TRENDING_UP") or (direction == "SHORT" and regime == "TRENDING_DOWN")) else 10 if direction != "NEUTRAL" else 5
    liquidity_score = 15 if len(swings) >= 3 else 8
    momentum_confirmed = (direction == "LONG" and profile.rsi_long_min <= rsi < profile.rsi_long_max) or (direction == "SHORT" and profile.rsi_short_min < rsi <= profile.rsi_short_max)
    momentum_score = 15 if momentum_confirmed else 7
    volume_confirmed = rel_vol >= profile.min_relative_volume
    volume_score = 10 if volume_confirmed else 5
    volatility_score = 10 if profile.low_volatility <= atr_pct <= profile.high_volatility else 5
    confidence = round(min(100, structure_score + htf_score + liquidity_score + momentum_score + volume_score + volatility_score + profile.confidence_delta))

    risk_buffer = max(atr * profile.atr_multiplier, current * profile.price_buffer)
    entry = current
    target_rr = effective_minimum_rr
    target_extension = target_rr + 1.0
    if direction == "LONG":
        sl, tp1, tp2 = current - risk_buffer, current + risk_buffer * target_rr, current + risk_buffer * target_extension
    elif direction == "SHORT":
        sl, tp1, tp2 = current + risk_buffer, current - risk_buffer * target_rr, current - risk_buffer * target_extension
    else:
        sl, tp1, tp2 = current - risk_buffer, current + risk_buffer * target_rr, current + risk_buffer * target_extension
    rr = abs(tp1 - entry) / max(abs(entry - sl), 1e-9)

    reasons = [f"ملف الأصل: {profile.label}"]
    if structure != "NEUTRAL":
        reasons.append(f"هيكل {structure}")
    if regime in ("TRENDING_UP", "TRENDING_DOWN"):
        reasons.append(f"نظام سوق {regime}")
    if volume_confirmed:
        reasons.append(f"الحجم مؤكد ≥ {profile.min_relative_volume:.2f}x")
    if momentum_confirmed:
        reasons.append("الزخم متوافق مع حدود الأصل")
    reasons.append(f"RR={rr:.2f}")
    reasons.append(profile.liquidity_note)

    signal = direction if direction != "NEUTRAL" and confidence >= effective_threshold and rr >= effective_minimum_rr else "NO TRADE"
    if signal == "NO TRADE":
        if direction == "NEUTRAL":
            reasons.append("لا يوجد توافق متعدد العوامل")
        if confidence < effective_threshold:
            reasons.append(f"الثقة أقل من {effective_threshold} الخاصة بهذا الأصل")
        if rr < effective_minimum_rr:
            reasons.append(f"العائد/المخاطرة أقل من {effective_minimum_rr:.1f}")

    return {
        **base,
        "price": _fmt(current),
        "regime": regime,
        "htf_trend": "BULLISH" if ema20 > ema50 else "BEARISH",
        "structure": structure,
        "bias": direction,
        "liquidity": "SWING CLUSTERS" if swings else "UNKNOWN",
        "momentum": "CONFIRMED" if momentum_score >= 15 else "WEAK",
        "volume": "CONFIRMED" if volume_score >= 10 else "WEAK",
        "relative_volume": round(rel_vol, 3),
        "atr_percent": round(atr_pct * 100, 3),
        "confidence": confidence,
        "signal": signal,
        "entry": _fmt(entry),
        "stop_loss": _fmt(sl),
        "take_profit_1": _fmt(tp1),
        "take_profit_2": _fmt(tp2),
        "rr": round(rr, 2),
        "reasons": reasons,
        "ready": signal in ("LONG", "SHORT"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
