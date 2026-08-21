from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


_INTERVAL_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
}


@dataclass(slots=True)
class SafetyAssessment:
    direction: str
    entry_price: float
    signal_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    entry_deviation_pct: float
    monitor_limit: float
    expected_rr_after_execution: float
    reversal_risk: int = 0
    reversal_risk_components: dict[str, int] = field(default_factory=dict)
    overextension_metrics: dict[str, Any] = field(default_factory=dict)
    warning_reasons: list[str] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)

    @property
    def would_block(self) -> bool:
        return bool(self.blocked_reasons)

    @property
    def state(self) -> str:
        if self.blocked_reasons:
            return "BLOCKED"
        if self.warning_reasons:
            return "WARN"
        return "ALLOW"

    def as_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "entry_price": self.entry_price,
            "signal_price": self.signal_price,
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "entry_deviation_pct": self.entry_deviation_pct,
            "monitor_limit": self.monitor_limit,
            "expected_rr_after_execution": self.expected_rr_after_execution,
            "reversal_risk": self.reversal_risk,
            "reversal_risk_components": dict(self.reversal_risk_components),
            "overextension_metrics": dict(self.overextension_metrics),
            "warning_reasons": list(self.warning_reasons),
            "blocked_reasons": list(self.blocked_reasons),
            "state": self.state,
        }


def interval_seconds(interval: str) -> int:
    return _INTERVAL_SECONDS.get(str(interval).lower(), 900)


def candle_close_epoch(candle: dict[str, Any], interval: str) -> float | None:
    try:
        return float(candle["time"]) + interval_seconds(interval)
    except (KeyError, TypeError, ValueError):
        return None


def is_candle_closed(candle: dict[str, Any], interval: str, now: float | None = None) -> bool:
    """Require both the exchange closed flag and a server-time close check."""
    if candle.get("closed") is False:
        return False
    close_epoch = candle_close_epoch(candle, interval)
    if close_epoch is None:
        return False
    return close_epoch <= (datetime.now(timezone.utc).timestamp() if now is None else now)


def closed_candles(candles: list[dict[str, Any]], interval: str, now: float | None = None) -> list[dict[str, Any]]:
    current = datetime.now(timezone.utc).timestamp() if now is None else now
    result = [c for c in candles if is_candle_closed(c, interval, current)]
    return sorted(result, key=lambda candle: float(candle.get("time", 0)))


def candle_age_seconds(candle: dict[str, Any], interval: str, now: float | None = None) -> float | None:
    close_epoch = candle_close_epoch(candle, interval)
    if close_epoch is None:
        return None
    current = datetime.now(timezone.utc).timestamp() if now is None else now
    return round(max(0.0, current - close_epoch), 3)


def iso_from_epoch(epoch: float | int | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()


def _valid_levels(direction: str, entry: float, stop_loss: float, tp1: float, tp2: float) -> bool:
    if direction == "LONG":
        return stop_loss < entry < tp1 < tp2
    if direction == "SHORT":
        return tp2 < tp1 < entry < stop_loss
    return False


def reprice_from_live_entry(signal: dict[str, Any], live_entry: float) -> dict[str, float]:
    """Move the paper levels by the signal risk distance to the fresh ticker price."""
    direction = str(signal.get("signal") or signal.get("direction") or "").upper()
    signal_entry = float(signal.get("entry") or signal.get("signal_price"))
    signal_stop = float(signal["stop_loss"])
    risk = abs(signal_entry - signal_stop)
    rr = max(float(signal.get("rr") or signal.get("risk_reward") or 0.0), 0.0)
    extension = rr + 1.0
    if direction == "LONG":
        return {
            "entry": live_entry,
            "stop_loss": live_entry - risk,
            "take_profit_1": live_entry + risk * rr,
            "take_profit_2": live_entry + risk * extension,
        }
    if direction == "SHORT":
        return {
            "entry": live_entry,
            "stop_loss": live_entry + risk,
            "take_profit_1": live_entry - risk * rr,
            "take_profit_2": live_entry - risk * extension,
        }
    raise ValueError("direction must be LONG or SHORT")


def assess_entry(
    signal: dict[str, Any],
    live_entry: float,
    minimum_rr: float,
    *,
    range_block: bool = True,
) -> SafetyAssessment:
    direction = str(signal.get("signal") or signal.get("direction") or "").upper()
    signal_price = float(signal.get("signal_price") or signal.get("price") or signal["entry"])
    levels = reprice_from_live_entry(signal, float(live_entry))
    entry = levels["entry"]
    risk = abs(entry - levels["stop_loss"])
    expected_rr = abs(levels["take_profit_1"] - entry) / max(risk, 1e-12)
    deviation = abs(entry - signal_price) / max(abs(signal_price), 1e-12)
    atr_percent = max(float(signal.get("atr_percent") or 0.0), 0.0) / 100.0
    monitor_limit = max(0.001, 0.15 * atr_percent)

    components = {key: int(value or 0) for key, value in (signal.get("reversal_risk_components") or {}).items()}
    reversal_risk = int(signal.get("reversal_risk") or sum(components.values()))
    metrics = dict(signal.get("overextension_metrics") or {})
    warnings: list[str] = []
    blocked: list[str] = []

    if deviation > monitor_limit:
        warnings.append("ENTRY_DEVIATION_ABOVE_SHADOW_LIMIT")
    if expected_rr < float(minimum_rr):
        blocked.append("EXPECTED_RR_AFTER_EXECUTION_BELOW_PROFILE_MINIMUM")
    if not _valid_levels(direction, entry, levels["stop_loss"], levels["take_profit_1"], levels["take_profit_2"]):
        blocked.append("INVALID_LEVEL_ORDER")
    if reversal_risk >= 2:
        warnings.append("REVERSAL_RISK_WARNING")
    if reversal_risk >= 3:
        blocked.append("REVERSAL_RISK_SHADOW_BLOCK")

    regime = str(signal.get("regime") or "")
    momentum = str(signal.get("momentum") or "").upper()
    breakout = bool(signal.get("breakout_confirmed"))
    retest = bool(signal.get("retest_confirmed"))
    if range_block and regime == "RANGING" and momentum == "WEAK" and not breakout and not retest:
        blocked.append("RANGING_WEAK_WITHOUT_BREAKOUT_OR_RETEST")

    room = metrics.get("room_to_resistance_atr") if direction == "LONG" else metrics.get("room_to_support_atr")
    if room is not None:
        try:
            room = float(room)
            if room < 0.75:
                warnings.append("OVEREXTENSION_WARNING")
            if room < 0.50 and not breakout:
                blocked.append("OVEREXTENSION_SHADOW_BLOCK")
        except (TypeError, ValueError):
            pass

    return SafetyAssessment(
        direction=direction,
        entry_price=round(entry, 8),
        signal_price=round(signal_price, 8),
        stop_loss=round(levels["stop_loss"], 8),
        take_profit_1=round(levels["take_profit_1"], 8),
        take_profit_2=round(levels["take_profit_2"], 8),
        entry_deviation_pct=round(deviation, 8),
        monitor_limit=round(monitor_limit, 8),
        expected_rr_after_execution=round(expected_rr, 4),
        reversal_risk=reversal_risk,
        reversal_risk_components=components,
        overextension_metrics=metrics,
        warning_reasons=warnings,
        blocked_reasons=blocked,
    )
