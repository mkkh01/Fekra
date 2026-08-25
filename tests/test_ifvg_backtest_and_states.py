from __future__ import annotations

import pytest

from app.strategies.ifvg.backtest import run_ifvg_backtest
from app.strategies.ifvg.states import InvalidIFVGTransition, validate_transition


def test_ifvg_state_machine_allows_lifecycle_and_rejects_skip():
    validate_transition("ENTRY_ELIGIBLE", "ORDER_INTENT")
    validate_transition("ORDER_INTENT", "ORDER_SUBMITTED")
    validate_transition("ORDER_SUBMITTED", "POSITION_OPEN")
    validate_transition("POSITION_OPEN", "STOP_TRIGGERED")
    with pytest.raises(InvalidIFVGTransition):
        validate_transition("FVG_DETECTED", "POSITION_OPEN")


def test_ifvg_backtest_is_fail_closed_with_missing_intervals():
    result = run_ifvg_backtest("BTCUSDT", {"5m": []})
    assert result["strategy_id"] == "IFVG_SPOT_V1_2"
    assert result["decisions"] == 0
    assert result["paper_only"] is True
    assert result["completed_trades"] == 0


def test_ifvg_backtest_never_marks_open_horizon_as_completed():
    rows = {interval: [] for interval in ("4h", "1h", "15m", "5m")}
    result = run_ifvg_backtest("BTCUSDT", rows)
    assert result["wins"] == 0
    assert result["losses"] == 0
    assert result["ambiguous_exits"] == 0
