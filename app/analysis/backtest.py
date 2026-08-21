from __future__ import annotations
from typing import Any
from .engine import analyze


def run_backtest(symbol: str, candles: list[dict[str, Any]], interval: str = "15m", fee_rate: float = 0.0004, slippage: float = 0.0002, threshold: int = 65, minimum_rr: float = 2.0, split: float = 0.7) -> dict[str, Any]:
    if len(candles) < 80:
        return {"error": "تحتاج المحاكاة إلى 80 شمعة على الأقل"}
    cut = max(60, int(len(candles) * split)); trades = []; equity = 0.0; peak = 0.0; max_dd = 0.0
    for i in range(cut, len(candles)):
        signal = analyze(symbol, candles[:i], interval, threshold, minimum_rr)
        if signal.get("signal") not in ("LONG", "SHORT"): continue
        entry = float(candles[i]["open"]); direction = signal["signal"]
        sl = float(signal["stop_loss"]); tp = float(signal["take_profit_1"]); exit_price = float(candles[i]["close"]); reason = "CLOSE"
        for future in candles[i:min(i + 20, len(candles))]:
            high, low = float(future["high"]), float(future["low"])
            if direction == "LONG":
                if low <= sl: exit_price, reason = sl, "STOPPED"; break
                if high >= tp: exit_price, reason = tp, "TP1"; break
            else:
                if high >= sl: exit_price, reason = sl, "STOPPED"; break
                if low <= tp: exit_price, reason = tp, "TP1"; break
        gross = ((exit_price - entry) / entry) if direction == "LONG" else ((entry - exit_price) / entry)
        net = gross - (fee_rate * 2) - slippage
        equity += net; peak = max(peak, equity); max_dd = max(max_dd, peak - equity)
        trades.append({"index": i, "direction": direction, "entry": entry, "exit": exit_price, "return": net, "reason": reason})
    wins = [t for t in trades if t["return"] > 0]; losses = [t for t in trades if t["return"] <= 0]
    gross_profit = sum(t["return"] for t in wins); gross_loss = abs(sum(t["return"] for t in losses))
    longest_loss = longest_win = current_loss = current_win = 0
    for t in trades:
        if t["return"] > 0: current_win += 1; current_loss = 0
        else: current_loss += 1; current_win = 0
        longest_loss = max(longest_loss, current_loss); longest_win = max(longest_win, current_win)
    return {"symbol": symbol, "interval": interval, "sample": "OUT_OF_SAMPLE", "total_trades": len(trades), "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else 0, "net_profit_pct": round(equity * 100, 3), "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else None, "expectancy_pct": round(equity / len(trades) * 100, 3) if trades else 0, "max_drawdown_pct": round(max_dd * 100, 3), "average_r": round(sum(t["return"] for t in trades) / len(trades), 4) if trades else 0, "longest_losing_streak": longest_loss, "longest_winning_streak": longest_win, "fees_and_slippage": "included", "trades": trades[-100:]}
