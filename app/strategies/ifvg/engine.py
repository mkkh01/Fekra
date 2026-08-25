from __future__ import annotations

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import math
from typing import Any, Iterable


STRATEGY_ID = "IFVG_SPOT_V1_2"
STRATEGY_VERSION = "1.2.1"
INTERVAL_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


@dataclass(frozen=True)
class IFVGConfig:
    strategy_id: str = STRATEGY_ID
    strategy_version: str = STRATEGY_VERSION
    macro_timeframe: str = "4h"
    structure_timeframe: str = "1h"
    setup_timeframe: str = "15m"
    confirmation_timeframe: str = "5m"
    pivot_left: int = 2
    pivot_right: int = 2
    equal_level_atr_multiplier: float = 0.05
    equal_level_tick_multiplier: int = 2
    sweep_min_depth_atr: float = 0.05
    sweep_max_depth_atr: float = 0.75
    sweep_to_inversion_min_bars: int = 1
    sweep_to_inversion_max_bars: int = 3
    displacement_min_range_atr: float = 1.20
    displacement_body_ratio_min: float = 0.60
    displacement_close_location_min: float = 0.80
    ifvg_max_age_bars: int = 24
    max_valid_retests: int = 1
    confirmation_window_5m_bars: int = 3
    confirmation_body_ratio_min: float = 0.50
    confirmation_close_location_min: float = 0.70
    max_entry_gap_pct: float = 0.005
    stop_buffer_atr: float = 0.10
    max_stop_atr: float = 2.50
    risk_per_trade: float = 0.005
    daily_loss_limit: float = 0.02
    minimum_gross_rr: float = 2.0
    minimum_net_rr: float = 2.0
    score_threshold: float = 80.0
    rvol_score_1: float = 1.20
    rvol_score_2: float = 1.50
    fee_bps: float = 10.0
    spread_bps: float = 4.0
    entry_slippage_bps: float = 2.0
    exit_slippage_bps: float = 2.0
    stop_slippage_bps: float = 4.0
    latency_bps: float = 0.0
    clock_skew_limit_ms: int = 1000
    history_days: dict[str, int] = field(default_factory=lambda: {"4h": 180, "1h": 180, "15m": 180, "5m": 180})

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.strategy_id != STRATEGY_ID:
            errors.append("STRATEGY_ID_INVARIANT")
        if self.strategy_version != STRATEGY_VERSION:
            errors.append("STRATEGY_VERSION_MISMATCH")
        if self.ifvg_max_age_bars <= 0:
            errors.append("INVALID_IFVG_MAX_AGE")
        if self.max_valid_retests != 1:
            errors.append("INVALID_MAX_RETESTS")
        if self.confirmation_window_5m_bars < 1:
            errors.append("INVALID_CONFIRMATION_WINDOW")
        if self.risk_per_trade <= 0:
            errors.append("INVALID_RISK_PER_TRADE")
        if self.minimum_net_rr < 2.0:
            errors.append("MINIMUM_NET_RR_BELOW_BASELINE")
        if not 0 <= self.score_threshold <= 100:
            errors.append("INVALID_SCORE_THRESHOLD")
        if self.sweep_min_depth_atr < 0 or self.sweep_max_depth_atr < self.sweep_min_depth_atr:
            errors.append("INVALID_SWEEP_RANGE")
        return errors

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Pivot:
    index: int
    time: float
    price: float
    kind: str
    confirmed_at: float
    atr_value: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _open_time(row: dict[str, Any]) -> float | None:
    return _num(row.get("open_time", row.get("time")))


def _close_time(row: dict[str, Any], interval: str) -> float | None:
    value = _num(row.get("close_time"))
    if value is not None:
        return value
    opened = _open_time(row)
    return None if opened is None else opened + INTERVAL_SECONDS[interval]


def _closed_rows(rows: Iterable[dict[str, Any]], interval: str, asof: float | None) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        if row.get("closed") is False:
            continue
        close_time = _close_time(row, interval)
        if close_time is None:
            continue
        if asof is not None and close_time > asof:
            continue
        if all(_num(row.get(key)) is not None for key in ("open", "high", "low", "close", "volume")):
            result.append({**row, "time": _open_time(row), "close_time": close_time, "closed": True})
    return sorted(result, key=lambda item: float(item["time"]))


def _data_integrity(rows: list[dict[str, Any]], interval: str) -> list[str]:
    failures: list[str] = []
    expected = INTERVAL_SECONDS[interval]
    times = [int(row["time"]) for row in rows if row.get("time") is not None]
    if len(times) != len(set(times)):
        failures.append(f"DUPLICATED_CANDLE_{interval}")
    for previous, current in zip(times, times[1:]):
        if current - previous != expected:
            failures.append(f"DATA_GAP_{interval}")
            break
    for row in rows:
        if any(_num(row.get(key)) is None for key in ("open", "high", "low", "close", "volume")):
            failures.append(f"MALFORMED_CANDLE_{interval}")
            break
        if row["high"] < max(row["open"], row["close"]) or row["low"] > min(row["open"], row["close"]):
            failures.append(f"MALFORMED_OHLC_{interval}")
            break
    return failures


def _true_range(current: dict[str, Any], previous_close: float | None) -> float:
    high, low = float(current["high"]), float(current["low"])
    if previous_close is None:
        return high - low
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def atr(rows: list[dict[str, Any]], period: int = 14, end_index: int | None = None) -> float | None:
    end = len(rows) if end_index is None else min(end_index, len(rows))
    if end < period:
        return None
    start = end - period
    values = []
    for index in range(start, end):
        previous_close = _num(rows[index - 1].get("close")) if index > 0 else None
        values.append(_true_range(rows[index], previous_close))
    return sum(values) / len(values) if values and all(math.isfinite(value) for value in values) else None


def _pivot_highs(rows: list[dict[str, Any]], left: int, right: int) -> list[Pivot]:
    pivots: list[Pivot] = []
    for index in range(left, len(rows) - right):
        high = float(rows[index]["high"])
        if all(high > float(rows[index - offset]["high"]) for offset in range(1, left + 1)) and all(high >= float(rows[index + offset]["high"]) for offset in range(1, right + 1)):
            pivots.append(Pivot(index, float(rows[index]["time"]), high, "HIGH", float(rows[index + right]["close_time"]), atr(rows, 14, index + 1)))
    return pivots


def _pivot_lows(rows: list[dict[str, Any]], left: int, right: int) -> list[Pivot]:
    pivots: list[Pivot] = []
    for index in range(left, len(rows) - right):
        low = float(rows[index]["low"])
        if all(low < float(rows[index - offset]["low"]) for offset in range(1, left + 1)) and all(low <= float(rows[index + offset]["low"]) for offset in range(1, right + 1)):
            pivots.append(Pivot(index, float(rows[index]["time"]), low, "LOW", float(rows[index + right]["close_time"]), atr(rows, 14, index + 1)))
    return pivots


def _structure(rows: list[dict[str, Any]], config: IFVGConfig, interval: str) -> dict[str, Any]:
    highs = _pivot_highs(rows, config.pivot_left, config.pivot_right)
    lows = _pivot_lows(rows, config.pivot_left, config.pivot_right)
    if len(highs) < 2 or len(lows) < 2:
        return {"state": "NEUTRAL", "anchor_pivot_time": None, "age_bars": None, "highs": highs, "lows": lows}
    previous_high, latest_high = highs[-2], highs[-1]
    previous_low, latest_low = lows[-2], lows[-1]
    if latest_high.price > previous_high.price and latest_low.price > previous_low.price:
        state = "BULLISH"
    elif latest_high.price < previous_high.price and latest_low.price < previous_low.price:
        state = "BEARISH"
    else:
        state = "NEUTRAL"
    anchor = max(latest_high.time, latest_low.time)
    newest = float(rows[-1]["time"])
    age_bars = max(0, int(round((newest - anchor) / INTERVAL_SECONDS[interval])))
    return {"state": state, "anchor_pivot_time": anchor, "age_bars": age_bars, "highs": highs, "lows": lows}


def _fvg_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index in range(2, len(rows)):
        c1, c2, c3 = rows[index - 2], rows[index - 1], rows[index]
        if float(c3["high"]) < float(c1["low"]):
            result.append({
                "index": index,
                "source_fvg_id": f"BEARISH:{int(c1['time'])}:{int(c2['time'])}:{int(c3['time'])}",
                "source_fvg_type": "BEARISH",
                "candle_times": [c1["time"], c2["time"], c3["time"]],
                "zone_low": float(c3["high"]),
                "zone_high": float(c1["low"]),
                "created_at": c3["close_time"],
                "state": "BEARISH_FVG_ACTIVE",
            })
    return result


def _displacement(rows: list[dict[str, Any]], index: int, config: IFVGConfig) -> dict[str, Any]:
    candle = rows[index]
    high, low = float(candle["high"]), float(candle["low"])
    opened, closed = float(candle["open"]), float(candle["close"])
    range_value = high - low
    prior_atr = atr(rows, 14, index)
    if range_value <= 0 or prior_atr is None or prior_atr <= 0:
        return {"valid": False, "reason": "INVALID_DISPLACEMENT", "range_atr": None, "body_ratio": None, "close_location": None}
    body_ratio = abs(closed - opened) / range_value
    close_location = (closed - low) / range_value
    range_atr = range_value / prior_atr
    valid = (
        closed > opened
        and range_atr >= config.displacement_min_range_atr
        and body_ratio >= config.displacement_body_ratio_min
        and close_location >= config.displacement_close_location_min
    )
    return {"valid": valid, "reason": None if valid else "INVALID_DISPLACEMENT", "range_atr": range_atr, "body_ratio": body_ratio, "close_location": close_location, "atr_prior": prior_atr}


def _latest_sweep(rows: list[dict[str, Any]], before_index: int, pivots: list[Pivot], config: IFVGConfig) -> dict[str, Any] | None:
    relevant = [pivot for pivot in pivots if pivot.index < before_index]
    for index in range(before_index - 1, -1, -1):
        levels = [pivot for pivot in relevant if pivot.index < index]
        if not levels:
            continue
        level = levels[-1]
        candle = rows[index]
        low = float(candle["low"])
        close = float(candle["close"])
        if low >= level.price or close <= level.price:
            continue
        current_atr = atr(rows, 14, index + 1)
        if current_atr is None or current_atr <= 0:
            continue
        depth = (level.price - low) / current_atr
        if config.sweep_min_depth_atr <= depth <= config.sweep_max_depth_atr:
            return {"index": index, "time": candle["time"], "low": low, "level": level.price, "depth": depth, "atr": current_atr, "pivot_time": level.time}
    return None


def _fill(reference: float, role: str, config: IFVGConfig, market: dict[str, Any]) -> tuple[float, dict[str, float]]:
    if role == "ENTRY":
        observed = _num(market.get("entry_ask", market.get("ask")))
        if observed is not None and observed > 0:
            return observed, {"spread": 0.0, "slippage": 0.0, "latency": 0.0}
        half_spread = reference * config.spread_bps / 20000.0
        slippage = reference * config.entry_slippage_bps / 10000.0
        latency = reference * config.latency_bps / 10000.0
        return reference + half_spread + slippage + latency, {"spread": half_spread, "slippage": slippage, "latency": latency}
    if role == "TARGET":
        observed = _num(market.get("target_bid", market.get("bid")))
        if observed is not None and observed > 0:
            return observed, {"spread": 0.0, "slippage": 0.0, "latency": 0.0}
        half_spread = reference * config.spread_bps / 20000.0
        slippage = reference * config.exit_slippage_bps / 10000.0
        latency = reference * config.latency_bps / 10000.0
        return reference - half_spread - slippage - latency, {"spread": half_spread, "slippage": slippage, "latency": latency}
    observed = _num(market.get("stop_bid", market.get("bid")))
    if observed is not None and observed > 0:
        return min(reference, observed), {"spread": 0.0, "slippage": 0.0, "latency": 0.0}
    half_spread = reference * config.spread_bps / 20000.0
    slippage = reference * config.stop_slippage_bps / 10000.0
    latency = reference * config.latency_bps / 10000.0
    return reference - half_spread - slippage - latency, {"spread": half_spread, "slippage": slippage, "latency": latency}


def _fee_per_unit(price: float, config: IFVGConfig) -> float:
    return price * config.fee_bps / 10000.0


def _floor_tick(value: float, tick_size: float) -> float:
    if tick_size <= 0:
        return value
    return math.floor((value + 1e-12) / tick_size) * tick_size


def _floor_step(value: float, step_size: float) -> float:
    if step_size <= 0:
        return value
    return math.floor((value + 1e-12) / step_size) * step_size


def _rvol(rows: list[dict[str, Any]], index: int, lookback: int = 20) -> float | None:
    if index < 1:
        return None
    history = rows[max(0, index - lookback):index]
    volumes = [_num(row.get("volume")) for row in history]
    volumes = [value for value in volumes if value is not None and value > 0]
    if not volumes:
        return None
    return float(rows[index]["volume"]) / (sum(volumes) / len(volumes))


def _hash_snapshot(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return sha256(encoded).hexdigest()


FAILURE_PRIORITY = [
    "NO_DATA", "CLOCK_SKEW_FAIL", "DATA_GAP_FAIL", "4H_BEARISH_VETO", "INVALID_STRUCTURE",
    "INVALID_SOURCE_FVG", "INVALID_SWEEP", "INVALID_DISPLACEMENT", "NO_INVERSION", "STALE_IFVG",
    "INVALID_RETEST", "NO_CONFIRMATION", "INVALID_STOP", "NO_TARGET", "GROSS_RR_FAIL",
    "NET_RR_FAIL", "EXECUTION_COST_FAIL", "ENTRY_GAP_FAIL", "BALANCE_FAIL", "POSITION_SIZE_FAIL",
    "POSITION_LIMIT_FAIL", "DAILY_RISK_FAIL", "EXECUTION_LIQUIDITY_FAIL", "PRECISION_FAIL",
    "SIDE_DIRECTION_FAIL", "CONFIG_UNAVAILABLE", "LOW_SCORE",
]
GATE_REASON = {
    "G03_4H_NOT_BEARISH": "4H_BEARISH_VETO", "G04_1H_BULLISH_STRUCTURE": "INVALID_STRUCTURE",
    "G08_RETEST_VALID": "INVALID_RETEST", "G09_CONFIRMATION_VALID": "NO_CONFIRMATION",
    "G10_CONFIRMATION_GATE": "NO_CONFIRMATION", "G12_VALID_STOP": "INVALID_STOP",
    "G13_VALID_TARGET": "NO_TARGET", "G14_GROSS_RR_OK": "GROSS_RR_FAIL", "G15_NET_RR_OK": "NET_RR_FAIL",
    "G16_EXECUTION_COST_OK": "EXECUTION_COST_FAIL", "G17_INVENTORY_BALANCE_OK": "BALANCE_FAIL",
    "G18_POSITION_LIMIT_OK": "POSITION_LIMIT_FAIL", "G19_DAILY_RISK_OK": "DAILY_RISK_FAIL",
    "G20_CLOCK_SKEW_OK": "CLOCK_SKEW_FAIL", "G21_SUFFICIENT_EXECUTION_LIQUIDITY": "EXECUTION_LIQUIDITY_FAIL",
    "G22_PRICE_PRECISION_OK": "PRECISION_FAIL", "G23_ENTRY_GAP_OK": "ENTRY_GAP_FAIL", "G24_SIDE_DIRECTION_INVARIANT": "SIDE_DIRECTION_FAIL",
}


def _primary(failures: list[str]) -> str | None:
    for code in FAILURE_PRIORITY:
        if code in failures or any(item.startswith(code + "_") for item in failures):
            return code
    if any(item.startswith("DATA_GAP_") for item in failures):
        return "DATA_GAP_FAIL"
    if any(item.startswith("NO_DATA_") for item in failures):
        return "NO_DATA"
    for failure in failures:
        if failure in GATE_REASON:
            return GATE_REASON[failure]
    return failures[0] if failures else None


def _gate(name: str, passed: bool, failed_gates: list[str], gates: dict[str, Any], detail: Any = None) -> None:
    gates[name] = {"passed": bool(passed), "detail": detail}
    if not passed:
        failed_gates.append(name)


def _candidate_targets(structure_1h: dict[str, Any], structure_4h: dict[str, Any], entry: float, config: IFVGConfig, tick_size: float) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for source, structure in (("1h", structure_1h), ("4h", structure_4h)):
        highs: list[Pivot] = structure.get("highs", [])
        for pivot in highs:
            if pivot.price > entry:
                values.append({"price": pivot.price, "source": source, "pivot_time": pivot.time, "quality": "SWING_HIGH"})
        grouped: list[list[Pivot]] = []
        for pivot in highs:
            if pivot.price <= entry:
                continue
            placed = False
            for group in grouped:
                atr_values = [item.atr_value for item in group + [pivot] if item.atr_value is not None and item.atr_value > 0]
                threshold = max(config.equal_level_tick_multiplier * tick_size, config.equal_level_atr_multiplier * max(atr_values, default=0.0))
                if abs(group[0].price - pivot.price) <= threshold:
                    group.append(pivot)
                    placed = True
                    break
            if not placed:
                grouped.append([pivot])
        for group in grouped:
            if len(group) >= 2:
                values.append({"price": max(item.price for item in group), "source": source, "pivot_time": max(item.time for item in group), "quality": "EQUAL_HIGH", "members": [item.time for item in group]})
    dedup: dict[tuple[str, float], dict[str, Any]] = {}
    for item in values:
        key = (item["source"], round(float(item["price"]), 12))
        dedup[key] = item
    return sorted(dedup.values(), key=lambda item: (float(item["price"]), item["source"], float(item["pivot_time"])))


def analyze_ifvg(
    symbol: str,
    rows_by_interval: dict[str, list[dict[str, Any]]],
    config: IFVGConfig | None = None,
    market: dict[str, Any] | None = None,
    portfolio: dict[str, Any] | None = None,
    asof: float | None = None,
) -> dict[str, Any]:
    config = config or IFVGConfig()
    market = dict(market or {})
    portfolio = dict(portfolio or {})
    config_errors = config.validate()
    normalized = {interval: _closed_rows(rows_by_interval.get(interval, []), interval, asof) for interval in INTERVAL_SECONDS}
    quality_failures: list[str] = []
    for interval, rows in normalized.items():
        if not rows:
            quality_failures.append(f"NO_DATA_{interval}")
        quality_failures.extend(_data_integrity(rows, interval))
    snapshot_payload = {
        "symbol": symbol.upper(),
        "strategy": config.strategy_id,
        "strategy_version": config.strategy_version,
        "asof": asof,
        "rows": {interval: rows for interval, rows in normalized.items()},
        "market": market,
        "portfolio": portfolio,
        "config": config.as_dict(),
    }
    data_snapshot_id = _hash_snapshot(snapshot_payload)
    result: dict[str, Any] = {
        "strategy": "IFVG_SPOT",
        "strategy_id": config.strategy_id,
        "strategy_version": config.strategy_version,
        "config_version": config.strategy_version,
        "symbol": symbol.upper(),
        "market": "SPOT",
        "side": "BUY",
        "direction": "LONG",
        "macro_timeframe": "4h",
        "structure_timeframe": "1h",
        "setup_timeframe": "15m",
        "confirmation_timeframe": "5m",
        "data_snapshot_id": data_snapshot_id,
        "data_version": data_snapshot_id,
        "snapshot_asof": {interval: (rows[-1]["close_time"] if rows else None) for interval, rows in normalized.items()},
        "hard_gates": {},
        "failed_gates": list(quality_failures),
        "score_components": {},
        "score": 0.0,
        "decision": "REJECTED",
        "state_sequence": [],
        "reasons": [],
    }
    if config_errors:
        result["failed_gates"].extend(config_errors)
        result["primary_rejection_reason"] = _primary(result["failed_gates"])
        result["reasons"].append("CONFIG_INVALID")
        return result

    rows_4h, rows_1h, rows_15m, rows_5m = (normalized[interval] for interval in ("4h", "1h", "15m", "5m"))
    _gate("G01_DATA_INTEGRITY", not quality_failures, result["failed_gates"], result["hard_gates"], quality_failures)
    _gate("G02_REQUIRED_TIMEFRAMES_AVAILABLE", all(len(normalized[interval]) >= 30 for interval in INTERVAL_SECONDS), result["failed_gates"], result["hard_gates"], {interval: len(normalized[interval]) for interval in INTERVAL_SECONDS})
    if quality_failures or any(len(normalized[interval]) < 30 for interval in INTERVAL_SECONDS):
        result["primary_rejection_reason"] = _primary(result["failed_gates"] + (["NO_DATA"] if not all(normalized.values()) else []))
        return result

    structure_4h = _structure(rows_4h, config, "4h")
    structure_1h = _structure(rows_1h, config, "1h")
    result["htf_4h_state"] = structure_4h["state"]
    result["structure_1h_state"] = structure_1h["state"]
    _gate("G03_4H_NOT_BEARISH", structure_4h["state"] != "BEARISH", result["failed_gates"], result["hard_gates"], structure_4h["state"])
    _gate("G04_1H_BULLISH_STRUCTURE", structure_1h["state"] == "BULLISH", result["failed_gates"], result["hard_gates"], structure_1h["state"])

    pivots_15m_lows = _pivot_lows(rows_15m, config.pivot_left, config.pivot_right)
    fvgs = _fvg_candidates(rows_15m)
    selected: dict[str, Any] | None = None
    invalid_sweep = False
    invalid_displacement = False
    for fvg in reversed(fvgs):
        if fvg["index"] >= len(rows_15m) - 1:
            continue
        inversion_index = None
        sweep = None
        displacement = None
        for candidate_index in range(fvg["index"] + 1, len(rows_15m)):
            if float(rows_15m[candidate_index]["close"]) <= fvg["zone_high"]:
                continue
            inversion_index = candidate_index
            displacement = _displacement(rows_15m, candidate_index, config)
            sweep = _latest_sweep(rows_15m, candidate_index, pivots_15m_lows, config)
            if sweep is None:
                invalid_sweep = True
                continue
            distance = candidate_index - sweep["index"]
            if not (config.sweep_to_inversion_min_bars <= distance <= config.sweep_to_inversion_max_bars):
                invalid_sweep = True
                continue
            if not displacement["valid"]:
                invalid_displacement = True
                continue
            selected = {**fvg, "inversion_index": inversion_index, "inversion_time": rows_15m[inversion_index]["close_time"], "sweep": sweep, "displacement": displacement}
            break
        if selected:
            break

    _gate("G05_VALID_SOURCE_BEARISH_FVG", bool(fvgs), result["failed_gates"], result["hard_gates"], len(fvgs))
    _gate("G06_VALID_SWEEP", selected is not None and not invalid_sweep, result["failed_gates"], result["hard_gates"], None if selected is None else selected["sweep"])
    _gate("G07_VALID_DISPLACEMENT", selected is not None and not invalid_displacement, result["failed_gates"], result["hard_gates"], None if selected is None else selected["displacement"])
    _gate("G08_FULL_BULLISH_INVERSION", selected is not None, result["failed_gates"], result["hard_gates"], None if selected is None else selected["inversion_time"])
    if not selected:
        result["primary_rejection_reason"] = _primary(result["failed_gates"] + (["INVALID_SOURCE_FVG"] if not fvgs else ["NO_INVERSION"]))
        return result

    result.update({
        "setup_id": selected["source_fvg_id"],
        "source_fvg_type": selected["source_fvg_type"],
        "source_fvg_candle_times": selected["candle_times"],
        "zone_low": selected["zone_low"],
        "zone_high": selected["zone_high"],
        "inverted_at": selected["inversion_time"],
        "sweep_low": selected["sweep"]["low"],
        "sweep_level": selected["sweep"]["level"],
        "sweep_depth": selected["sweep"]["depth"],
        "sweep_time": selected["sweep"]["time"],
        "displacement_range_atr": selected["displacement"]["range_atr"],
        "displacement_body_ratio": selected["displacement"]["body_ratio"],
        "displacement_close_location": selected["displacement"]["close_location"],
    })
    inversion_index = selected["inversion_index"]
    age_now = max(0, len(rows_15m) - inversion_index - 1)
    _gate("G09_IFVG_FRESH", age_now < config.ifvg_max_age_bars, result["failed_gates"], result["hard_gates"], age_now)
    if age_now >= config.ifvg_max_age_bars:
        result["primary_rejection_reason"] = "STALE_IFVG"
        result["state_sequence"] = ["FVG_DETECTED", "FVG_ACTIVE", "INVERTED", "IFVG_ACTIVE", "EXPIRED"]
        return result

    retest = None
    invalidated = False
    for index in range(inversion_index + 1, min(len(rows_15m), inversion_index + config.ifvg_max_age_bars)):
        candle = rows_15m[index]
        if float(candle["close"]) < selected["zone_low"]:
            invalidated = True
            break
        if float(candle["low"]) <= selected["zone_high"]:
            retest = {"index": index, "time": candle["close_time"], "close": candle["close"], "low": candle["low"], "count": 1}
            break
    _gate("G10_FIRST_RETEST", retest is not None and not invalidated, result["failed_gates"], result["hard_gates"], retest)
    if not retest:
        result["primary_rejection_reason"] = "INVALID_RETEST" if invalidated else "NO_CONFIRMATION"
        result["state_sequence"] = ["FVG_DETECTED", "FVG_ACTIVE", "INVERTED", "IFVG_ACTIVE", "WAITING_RETEST", "INVALIDATED" if invalidated else "WAITING_RETEST"]
        return result

    result.update({"retest_start_time": retest["time"], "first_retest_at": retest["time"], "retest_count": 1, "age_bars": retest["index"] - inversion_index})
    confirmations = [row for row in rows_5m if float(row["close_time"]) > float(retest["time"])]
    confirmations = confirmations[:config.confirmation_window_5m_bars]
    confirmation = None
    for row in confirmations:
        high, low, opened, closed = map(float, (row["high"], row["low"], row["open"], row["close"]))
        range_value = high - low
        if range_value <= 0:
            continue
        body_ratio = abs(closed - opened) / range_value
        location = (closed - low) / range_value
        if closed > opened and body_ratio >= config.confirmation_body_ratio_min and location >= config.confirmation_close_location_min and closed >= selected["zone_low"]:
            confirmation = {"row": row, "body_ratio": body_ratio, "close_location": location}
            break
    _gate("G11_5M_CONFIRMATION", confirmation is not None, result["failed_gates"], result["hard_gates"], None if confirmation is None else confirmation["row"]["time"])
    if confirmation is None:
        result["primary_rejection_reason"] = "NO_CONFIRMATION"
        result["state_sequence"] = ["FVG_DETECTED", "FVG_ACTIVE", "INVERTED", "IFVG_ACTIVE", "WAITING_RETEST", "RETEST_DETECTED", "WAITING_CONFIRMATION", "REJECTED"]
        return result

    confirmation_row = confirmation["row"]
    confirmation_index = rows_5m.index(confirmation_row)
    next_row = rows_5m[confirmation_index + 1] if confirmation_index + 1 < len(rows_5m) else None
    expected_entry = float(confirmation_row["close"])
    reference_next_open = None if next_row is None else float(next_row["open"])
    next_open_time = None if next_row is None else float(next_row["time"])
    if reference_next_open is None:
        reference_next_open = _num(market.get("next_5m_open"))
        next_open_time = _num(market.get("next_5m_open_time"))
    result.update({
        "confirmation_5m": {"time": confirmation_row["time"], "body_ratio": confirmation["body_ratio"], "close_location": confirmation["close_location"]},
        "expected_entry": expected_entry,
        "reference_next_open": reference_next_open,
        "signal_candle_time": confirmation_row["time"],
    })
    gap_pct = None if reference_next_open is None or expected_entry <= 0 else abs(reference_next_open - expected_entry) / expected_entry
    _gate("G23_ENTRY_GAP_OK", gap_pct is not None and gap_pct <= config.max_entry_gap_pct, result["failed_gates"], result["hard_gates"], gap_pct)
    if reference_next_open is None:
        result["primary_rejection_reason"] = "ENTRY_GAP_FAIL"
        return result

    decision_time = next_open_time or float(confirmation_row["close_time"])
    atr_entry = atr(rows_15m, 14, len(rows_15m))
    if atr_entry is None or atr_entry <= 0:
        result["failed_gates"].append("ATR_UNAVAILABLE")
        result["primary_rejection_reason"] = "INVALID_STOP"
        return result
    raw_stop = min(selected["zone_low"], selected["sweep"]["low"]) - config.stop_buffer_atr * atr_entry
    tick_size = _num(market.get("tick_size"), 0.0) or 0.0
    stop_price = _floor_tick(raw_stop, tick_size)
    _gate("G12_VALID_STOP", stop_price > 0 and (reference_next_open - stop_price) / atr_entry <= config.max_stop_atr, result["failed_gates"], result["hard_gates"], {"raw": raw_stop, "price": stop_price, "atr": atr_entry})
    if not result["hard_gates"]["G12_VALID_STOP"]["passed"]:
        result["primary_rejection_reason"] = "INVALID_STOP"
        return result

    entry_fill, entry_cost = _fill(reference_next_open, "ENTRY", config, market)
    targets = _candidate_targets(structure_1h, structure_4h, entry_fill, config, tick_size)
    selected_target = None
    rejected_nearest = False
    target_fill = None
    stop_fill, stop_cost = _fill(stop_price, "STOP", config, market)
    entry_fee_unit = _fee_per_unit(entry_fill, config)
    stop_fee_unit = _fee_per_unit(stop_fill, config)
    risk_per_unit = entry_fill - stop_fill + entry_fee_unit + stop_fee_unit
    for candidate in targets:
        target_price = _floor_tick(float(candidate["price"]), tick_size)
        if target_price <= entry_fill:
            continue
        candidate_target_fill, target_cost = _fill(target_price, "TARGET", config, market)
        gross_rr = (candidate_target_fill - entry_fill) / max(entry_fill - stop_fill, 1e-12)
        target_fee_unit = _fee_per_unit(candidate_target_fill, config)
        net_profit = candidate_target_fill - entry_fill - entry_fee_unit - target_fee_unit
        net_rr = net_profit / max(risk_per_unit, 1e-12)
        if selected_target is None and net_rr < config.minimum_net_rr:
            rejected_nearest = True
            continue
        selected_target = {**candidate, "price": target_price, "fill": candidate_target_fill, "gross_rr": gross_rr, "net_rr": net_rr, "fee_unit": target_fee_unit, "cost": target_cost}
        break
    _gate("G13_VALID_TARGET", selected_target is not None, result["failed_gates"], result["hard_gates"], len(targets))
    if selected_target is None:
        result["primary_rejection_reason"] = "NO_TARGET"
        return result

    target_fill = float(selected_target["fill"])
    gross_rr = float(selected_target["gross_rr"])
    net_rr = float(selected_target["net_rr"])
    _gate("G14_GROSS_RR_OK", gross_rr >= config.minimum_gross_rr, result["failed_gates"], result["hard_gates"], gross_rr)
    _gate("G15_NET_RR_OK", net_rr >= config.minimum_net_rr, result["failed_gates"], result["hard_gates"], net_rr)
    _gate("G16_EXECUTION_COST_OK", all(value >= 0 for value in (entry_cost["spread"], stop_cost["spread"], selected_target["cost"]["spread"])), result["failed_gates"], result["hard_gates"], {"entry": entry_cost, "stop": stop_cost, "target": selected_target["cost"]})
    result.update({
        "entry_fill": entry_fill,
        "stop_price": stop_price,
        "stop_fill": stop_fill,
        "target_price": selected_target["price"],
        "target_fill": target_fill,
        "target_fill_gross": target_fill,
        "gross_rr": gross_rr,
        "net_rr": net_rr,
        "atr_reference_time": rows_15m[-1]["close_time"],
        "atr_value": atr_entry,
        "risk_per_unit_quote": risk_per_unit,
        "entry_fee_per_unit_quote": entry_fee_unit,
        "stop_fee_per_unit_quote": stop_fee_unit,
        "target_fee_per_unit_quote": selected_target["fee_unit"],
        "fee_rate_bps": config.fee_bps,
        "target_quality_points": 3 if rejected_nearest else 5,
        "fill_components": {"entry": entry_cost, "stop": stop_cost, "target": selected_target["cost"]},
    })

    balance = _num(portfolio.get("quote_balance"))
    equity = _num(portfolio.get("eligible_equity"), balance)
    tick_ok = tick_size > 0
    step_size = _num(market.get("step_size"), 0.0) or 0.0
    min_notional = _num(market.get("min_notional"), 0.0) or 0.0
    _gate("G17_INVENTORY_BALANCE_OK", balance is not None and balance > 0 and equity is not None and equity > 0, result["failed_gates"], result["hard_gates"], {"quote_balance": balance, "equity": equity})
    _gate("G18_POSITION_LIMIT_OK", portfolio.get("max_position_value_quote") is not None and portfolio.get("max_global_open_positions") is not None, result["failed_gates"], result["hard_gates"], portfolio)
    daily_loss = _num(portfolio.get("daily_loss_fraction"), 0.0) or 0.0
    _gate("G19_DAILY_RISK_OK", daily_loss < config.daily_loss_limit, result["failed_gates"], result["hard_gates"], daily_loss)
    clock_value = _num(portfolio.get("clock_skew_ms"))
    clock_skew = abs(clock_value) if clock_value is not None else None
    _gate("G20_CLOCK_SKEW_OK", clock_skew is not None and clock_skew <= config.clock_skew_limit_ms, result["failed_gates"], result["hard_gates"], clock_skew)
    _gate("G21_SUFFICIENT_EXECUTION_LIQUIDITY", bool(portfolio.get("execution_liquidity_ok", True)), result["failed_gates"], result["hard_gates"], portfolio.get("liquidity_subchecks", []))
    _gate("G22_PRICE_PRECISION_OK", tick_ok and step_size > 0 and min_notional >= 0, result["failed_gates"], result["hard_gates"], {"tick_size": tick_size, "step_size": step_size, "min_notional": min_notional})
    _gate("G24_SIDE_DIRECTION_INVARIANT", result["side"] == "BUY" and result["direction"] == "LONG", result["failed_gates"], result["hard_gates"], None)

    rvol = _rvol(rows_5m, confirmation_index)
    result["rvol"] = rvol
    components = {
        "4h_context": 20 if structure_4h["state"] == "BULLISH" else 10,
        "1h_structure": 15 if structure_1h["state"] == "BULLISH" else 0,
        "sweep_quality": 15 if selected["sweep"]["depth"] < 0.30 else 12,
        "displacement_quality": 15 if selected["displacement"]["range_atr"] >= 1.50 else 12,
        "ifvg_quality": 15 if result["age_bars"] <= 6 else 12 if result["age_bars"] <= 12 else 8,
        "first_retest": 10,
        "volume_quality": 5 if rvol is not None and rvol >= config.rvol_score_2 else 3 if rvol is not None and rvol >= config.rvol_score_1 else 0,
        "target_quality": result["target_quality_points"],
    }
    result["score_components"] = components
    result["score"] = float(sum(components.values()))
    hard_failed = [name for name, value in result["hard_gates"].items() if not value["passed"]]
    if hard_failed:
        result["primary_rejection_reason"] = _primary(result["failed_gates"])
        result["state_sequence"] = ["FVG_DETECTED", "FVG_ACTIVE", "INVERTED", "IFVG_ACTIVE", "WAITING_RETEST", "RETEST_DETECTED", "WAITING_CONFIRMATION", "CONFIRMED", "REJECTED"]
        return result

    _gate("G_SCORE_THRESHOLD", result["score"] >= config.score_threshold, result["failed_gates"], result["hard_gates"], result["score"])
    risk_amount = None if equity is None else equity * config.risk_per_trade
    raw_qty = None if risk_amount is None or risk_per_unit <= 0 else risk_amount / risk_per_unit
    max_position_value = _num(portfolio.get("max_position_value_quote"))
    max_global_positions = _num(portfolio.get("max_global_open_positions"))
    qty = raw_qty
    if qty is not None and max_position_value is not None:
        qty = min(qty, max_position_value / max(entry_fill, 1e-12))
    if qty is not None and balance is not None:
        qty = min(qty, balance / max(entry_fill + entry_fee_unit, 1e-12))
    qty = None if qty is None else _floor_step(qty, step_size)
    notional = None if qty is None else qty * entry_fill
    realized_risk = None if qty is None else qty * risk_per_unit
    _gate("G_POSITION_SIZE", qty is not None and qty > 0 and (min_notional <= 0 or (notional or 0) >= min_notional) and (risk_amount is None or (realized_risk or 0) <= risk_amount + 1e-9), result["failed_gates"], result["hard_gates"], {"raw_qty": raw_qty, "quantity": qty, "notional": notional, "realized_risk": realized_risk})
    result.update({"risk_amount_quote": risk_amount, "position_size": qty, "entry_notional_quote": notional, "realized_risk_quote": realized_risk})
    if not result["hard_gates"]["G_SCORE_THRESHOLD"]["passed"]:
        result["primary_rejection_reason"] = "LOW_SCORE"
    elif not result["hard_gates"]["G_POSITION_SIZE"]["passed"]:
        result["primary_rejection_reason"] = "POSITION_SIZE_FAIL"
    else:
        result["decision"] = "ENTRY_ELIGIBLE"
        result["primary_rejection_reason"] = None
    result["state_sequence"] = ["FVG_DETECTED", "FVG_ACTIVE", "INVERTED", "IFVG_ACTIVE", "WAITING_RETEST", "RETEST_DETECTED", "WAITING_CONFIRMATION", "CONFIRMED", "ENTRY_ELIGIBLE" if result["decision"] == "ENTRY_ELIGIBLE" else "REJECTED"]
    return result
