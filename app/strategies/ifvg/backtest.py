from __future__ import annotations

from collections import Counter
from typing import Any

from .engine import IFVGConfig, analyze_ifvg


def _simulate_exit(rows: list[dict[str, Any]], entry_index: int, entry: float, stop: float, target: float, quantity: float, fee_bps: float) -> dict[str, Any]:
    entry_fee = entry * quantity * fee_bps / 10000.0
    for row in rows[entry_index:]:
        low, high = float(row["low"]), float(row["high"])
        stop_hit, target_hit = low <= stop, high >= target
        if not stop_hit and not target_hit:
            continue
        if stop_hit:
            exit_price, result, reason, ambiguous = stop, "LOSS", "STOP_TRIGGERED", bool(target_hit)
        else:
            exit_price, result, reason, ambiguous = target, "WIN", "TP_FILLED", False
        exit_fee = exit_price * quantity * fee_bps / 10000.0
        pnl = (exit_price - entry) * quantity - entry_fee - exit_fee
        return {"result": result, "reason": reason, "exit_price": exit_price, "pnl_quote": pnl, "ambiguous": ambiguous, "exit_time": row.get("time")}
    return {"result": "OPEN", "reason": "HORIZON_EXPIRED", "exit_price": None, "pnl_quote": None, "ambiguous": False, "exit_time": None}


def run_ifvg_backtest(
    symbol: str,
    rows_by_interval: dict[str, list[dict[str, Any]]],
    config: IFVGConfig | None = None,
    market: dict[str, Any] | None = None,
    portfolio: dict[str, Any] | None = None,
    max_decisions: int = 5000,
) -> dict[str, Any]:
    config = config or IFVGConfig()
    market = dict(market or {})
    portfolio = dict(portfolio or {})
    rows_5m = sorted(rows_by_interval.get("5m", []), key=lambda row: row.get("time", 0))
    decisions: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    seen_signals: set[tuple[str, str | None]] = set()
    rejection_counts: Counter[str] = Counter()
    entry_count = 0
    for index in range(30, max(30, len(rows_5m) - 1)):
        confirmation_boundary = float(rows_5m[index]["time"])
        next_row = rows_5m[index + 1]
        prefix = {}
        for interval, rows in rows_by_interval.items():
            prefix[interval] = [row for row in rows if float(row.get("close_time", row.get("time", 0))) <= confirmation_boundary]
        decision_market = {**market, "next_5m_open": next_row.get("open"), "next_5m_open_time": next_row.get("time")}
        result = analyze_ifvg(symbol, prefix, config=config, market=decision_market, portfolio=portfolio, asof=confirmation_boundary)
        key = (str(result.get("setup_id")), result.get("signal_candle_time"))
        if key in seen_signals:
            continue
        seen_signals.add(key)
        decisions.append(result)
        reason = result.get("primary_rejection_reason")
        if reason:
            rejection_counts[str(reason)] += 1
        if result.get("decision") != "ENTRY_ELIGIBLE":
            continue
        entry_count += 1
        quantity = float(result.get("position_size") or 0)
        outcomes.append({
            "signal": result,
            "outcome": _simulate_exit(rows_5m, index + 1, float(result["entry_fill"]), float(result["stop_price"]), float(result["target_price"]), quantity, config.fee_bps),
        })
        if entry_count >= max_decisions:
            break
    completed = [item["outcome"] for item in outcomes if item["outcome"]["result"] in {"WIN", "LOSS"}]
    wins = sum(item["result"] == "WIN" for item in completed)
    losses = sum(item["result"] == "LOSS" for item in completed)
    total_pnl = sum(float(item.get("pnl_quote") or 0) for item in completed)
    return {
        "strategy_id": config.strategy_id,
        "strategy_version": config.strategy_version,
        "symbol": symbol.upper(),
        "paper_only": True,
        "decisions": len(decisions),
        "entry_eligible": entry_count,
        "completed_trades": len(completed),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / len(completed) * 100, 4) if completed else 0.0,
        "total_pnl_quote": round(total_pnl, 8),
        "ambiguous_exits": sum(bool(item.get("ambiguous")) for item in completed),
        "rejection_counts": dict(rejection_counts),
        "outcomes": outcomes,
        "data_snapshot_policy": "closed-prefix-per-5m-decision",
        "warnings": ["Backtest output is not evidence of future profitability.", "No live exchange orders are used."],
    }
