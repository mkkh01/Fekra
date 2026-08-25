from __future__ import annotations

from typing import Any

from .engine import analyze
from .safety import assess_entry


def _stats(trades: list[dict[str, Any]], blocked: list[dict[str, Any]], symbol: str, interval: str, sample: str) -> dict[str, Any]:
    wins = [trade for trade in trades if trade["return"] > 0]
    losses = [trade for trade in trades if trade["return"] <= 0]
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    longest_loss = longest_win = current_loss = current_win = 0
    for trade in trades:
        equity += trade["return"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if trade["return"] > 0:
            current_win += 1
            current_loss = 0
        else:
            current_loss += 1
            current_win = 0
        longest_loss = max(longest_loss, current_loss)
        longest_win = max(longest_win, current_win)
    gross_profit = sum(trade["return"] for trade in wins)
    gross_loss = abs(sum(trade["return"] for trade in losses))
    return {
        "symbol": symbol,
        "interval": interval,
        "sample": sample,
        "total_trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else 0,
        "net_profit_pct": round(equity * 100, 3),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None,
        "expectancy_pct": round(equity / len(trades) * 100, 3) if trades else 0,
        "max_drawdown_pct": round(max_dd * 100, 3),
        "average_r": round(sum(trade["return"] for trade in trades) / len(trades), 4) if trades else 0,
        "longest_losing_streak": longest_loss,
        "longest_winning_streak": longest_win,
        "blocked_signals": len(blocked),
        "blocked_winners": sum(1 for item in blocked if item.get("return", 0) > 0),
        "blocked_losses": sum(1 for item in blocked if item.get("return", 0) <= 0),
        "fees_and_slippage": "included",
        "trades": trades[-100:],
        "blocked": blocked[-100:],
    }


def _simulate_one(candles: list[dict[str, Any]], index: int, signal: dict[str, Any], fee_rate: float, slippage: float) -> dict[str, Any]:
    direction = signal["signal"]
    entry = float(candles[index]["open"])
    signal_entry = float(signal.get("signal_price") or signal["entry"])
    risk = abs(float(signal["entry"]) - float(signal["stop_loss"]))
    if direction == "LONG":
        sl, tp = entry - risk, entry + risk * float(signal.get("rr", 2.0))
    else:
        sl, tp = entry + risk, entry - risk * float(signal.get("rr", 2.0))
    exit_price = float(candles[index]["close"])
    reason = "CLOSE"
    for future in candles[index:min(index + 20, len(candles))]:
        high, low = float(future["high"]), float(future["low"])
        if direction == "LONG":
            if low <= sl:
                exit_price, reason = sl, "STOPPED"
                break
            if high >= tp:
                exit_price, reason = tp, "TP1"
                break
        else:
            if high >= sl:
                exit_price, reason = sl, "STOPPED"
                break
            if low <= tp:
                exit_price, reason = tp, "TP1"
                break
    gross = ((exit_price - entry) / max(abs(entry), 1e-12)) if direction == "LONG" else ((entry - exit_price) / max(abs(entry), 1e-12))
    net = gross - (fee_rate * 2) - slippage
    return {
        "index": index,
        "direction": direction,
        "entry": entry,
        "signal_price": signal_entry,
        "exit": exit_price,
        "return": net,
        "reason": reason,
    }


def _candle_time(candle: dict[str, Any]) -> float:
    value = candle.get("time", candle.get("open_time", 0))
    try:
        numeric = float(value)
        return numeric / 1000 if numeric > 100_000_000_000 else numeric
    except (TypeError, ValueError):
        return 0.0


def run_backtest(
    symbol: str,
    candles: list[dict[str, Any]],
    interval: str = "15m",
    fee_rate: float = 0.0004,
    slippage: float = 0.0002,
    threshold: int = 65,
    minimum_rr: float = 2.0,
    split: float = 0.7,
    mtf_candles: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    if len(candles) < 80:
        return {"error": "تحتاج المحاكاة إلى 80 شمعة على الأقل"}
    cut = max(60, int(len(candles) * split))
    trades: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, int | None]] = set()
    for index in range(cut, len(candles)):
        history = candles[:index]
        signal = analyze(symbol, history, interval, threshold, minimum_rr)
        if signal.get("signal") not in ("LONG", "SHORT"):
            continue
        if mtf_candles:
            current_time = _candle_time(candles[index])
            aligned = True
            for higher_interval in ("1h", "4h"):
                higher_history = [candle for candle in mtf_candles.get(higher_interval, []) if _candle_time(candle) < current_time]
                higher_signal = analyze(symbol, higher_history, higher_interval, threshold, minimum_rr) if len(higher_history) >= 80 else {"signal": None, "ready": False}
                if not higher_signal.get("ready") or higher_signal.get("signal") != signal.get("signal"):
                    aligned = False
                    break
            if not aligned:
                continue
        live_entry = float(candles[index]["open"])
        safety = assess_entry(signal, live_entry, signal.get("applied_minimum_rr", minimum_rr))
        candle_time = signal.get("signal_candle_time")
        key = (symbol, interval, int(float(candle_time.timestamp())) if hasattr(candle_time, "timestamp") else candle_time)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        simulated = _simulate_one(candles, index, signal, fee_rate, slippage)
        blocked_item = {**simulated, "blocked_reasons": safety.blocked_reasons, "warning_reasons": safety.warning_reasons}
        if safety.would_block:
            blocked.append(blocked_item)
        else:
            trades.append(simulated)
    result = _stats(trades, blocked, symbol, interval, "OUT_OF_SAMPLE")
    result["mtf_alignment_applied"] = bool(mtf_candles)
    result["walk_forward"] = {
        "in_sample_cut_index": cut,
        "out_of_sample_start": cut,
        "out_of_sample_end": len(candles),
        "split": split,
        "blocked_rate_pct": round(len(blocked) / max(len(trades) + len(blocked), 1) * 100, 3),
    }
    return result
