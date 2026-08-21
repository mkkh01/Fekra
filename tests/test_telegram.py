import asyncio

from app.telegram import TelegramBotController, TelegramNotifier


class FakeStore:
    backend_name = "postgres"
    has_persistent_storage = True

    async def list_active_trades(self):
        return [{
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "status": "OPEN",
            "entry": 100,
            "stop_loss": 98,
            "take_profit_1": 104,
            "created_at": "2026-08-19T20:00:00+00:00",
        }]

    async def list_trades(self, status=None):
        return [{
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "status": "CLOSED",
            "result": "WIN",
            "exit_reason": "TAKE_PROFIT_1",
            "exit_price": 104,
            "pnl": 4,
            "closed_at": "2026-08-19T20:10:00+00:00",
        }]


class FakeMarket:
    tickers = {"BTCUSDT": {"price": 102.5, "change": 1.2}}

    def health_snapshot(self):
        return {"live_feed": True}


class FakeSettings:
    symbol_list = ["BTCUSDT"]


def controller():
    notifier = TelegramNotifier("token", "1503808643")
    return TelegramBotController(
        notifier,
        FakeStore(),
        FakeMarket(),
        FakeSettings(),
        {
            "status": "IDLE",
            "completed_cycles": 3,
            "scanned_symbols": 10,
            "ready_signals": 1,
            "saved_trades": 0,
            "last_saved_symbols": [],
            "next_run_at": None,
            "last_error": None,
        },
    )


def test_telegram_disabled_does_not_send():
    result = asyncio.run(TelegramNotifier(None, None).send_message("test"))
    assert result == {"sent": 0, "failed": 0}


def test_trade_open_message_contains_mtf_snapshot():
    notifier = TelegramNotifier(None, None)
    message = notifier.format_trade_opened({
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "timeframe": "15m",
        "entry": 100,
        "stop_loss": 98,
        "take_profit_1": 104,
        "take_profit_2": 106,
        "risk_reward": 2,
        "confidence": 82,
        "mtf_alignment": "ALIGNED",
        "mtf_timeframes": {
            "4h": {"signal": "LONG"},
            "1h": {"signal": "LONG"},
            "15m": {"signal": "LONG"},
        },
    })
    assert "BTCUSDT" in message
    assert "MTF: ALIGNED" in message
    assert "4h=LONG" in message


def test_trade_close_message_contains_exit_data():
    notifier = TelegramNotifier(None, None)
    message = notifier.format_trade_closed({
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "exit_reason": "TAKE_PROFIT_1",
        "entry": 100,
        "exit_price": 104,
        "result": "WIN",
        "pnl": 4,
        "closed_at": "2026-08-19T20:00:00+00:00",
    })
    assert "سعر الخروج: 104" in message
    assert "النتيجة: WIN" in message
    assert "TAKE_PROFIT_1" in message


def test_menu_contains_requested_buttons():
    rows = controller().menu_markup()["inline_keyboard"]
    labels = [button["text"] for row in rows for button in row]
    assert "الصفقات المفتوحة" in labels
    assert "الصفقات المغلقة" in labels
    assert "الأسعار الحالية" in labels
    assert "Summary Cycle" in labels
    assert "أداء النظام" in labels


def test_controller_only_authorizes_configured_chat():
    bot = controller()
    assert bot.is_authorized("1503808643") is True
    assert bot.is_authorized("different-chat") is False


def test_controller_renders_live_prices_and_performance():
    bot = controller()
    prices = bot.render_prices()
    performance = asyncio.run(bot.render_performance())
    assert "BTCUSDT" in prices
    assert "102.50" in prices
    assert "نسبة الفوز: 100.00%" in performance
    assert "إجمالي PnL المسجل: 4.0000%" in performance
