from __future__ import annotations

import main
from app.telegram import TelegramBotController


def test_ifvg_dashboard_routes_are_separate():
    paths = {route.path for route in main.app.routes}
    assert "/api/ifvg/cycle/summary" in paths
    assert "/api/ifvg/trades/open" in paths
    assert "/api/ifvg/trades/closed" in paths
    assert "/api/ifvg/performance" in paths


def test_telegram_menu_contains_four_ifvg_controls():
    callbacks = {
        button["callback_data"]
        for row in TelegramBotController.menu_markup()["inline_keyboard"]
        for button in row
        if "callback_data" in button
    }
    assert {"ifvg_cycle", "ifvg_open", "ifvg_closed", "ifvg_performance"}.issubset(callbacks)
