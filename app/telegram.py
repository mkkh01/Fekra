from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger("weeg.telegram")


class TelegramNotifier:
    def __init__(self, bot_token: str | None, chat_id: str | None):
        self.bot_token = (bot_token or "").strip() or None
        self.chat_id = (chat_id or "").strip() or None

    @property
    def configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    async def _api_call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "description": "Telegram غير مهيأ"}
        async with httpx.AsyncClient(timeout=35) as client:
            response = await client.post(self._url(method), json=payload)
            response.raise_for_status()
            data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API {method}: {data.get('description', 'unknown error')}")
        return data

    async def send_message(
        self,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        chat_id: str | None = None,
    ) -> dict[str, int]:
        if not self.configured:
            return {"sent": 0, "failed": 0}
        payload: dict[str, Any] = {
            "chat_id": chat_id or self.chat_id,
            "text": text[:4090],
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            await self._api_call("sendMessage", payload)
            return {"sent": 1, "failed": 0}
        except Exception as exc:
            log.warning("telegram delivery failed: %s", type(exc).__name__)
            return {"sent": 0, "failed": 1}

    async def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        data = await self._api_call("getUpdates", payload)
        return data.get("result", [])

    async def answer_callback_query(self, callback_query_id: str) -> None:
        try:
            await self._api_call("answerCallbackQuery", {"callback_query_id": callback_query_id})
        except Exception as exc:
            log.warning("telegram callback acknowledgement failed: %s", type(exc).__name__)

    async def edit_message(self, chat_id: str, message_id: int, text: str, reply_markup: dict[str, Any]) -> None:
        try:
            await self._api_call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": text[:4090],
                    "disable_web_page_preview": True,
                    "reply_markup": reply_markup,
                },
            )
        except Exception as exc:
            log.warning("telegram message edit failed: %s", type(exc).__name__)

    @staticmethod
    def format_trade_opened(trade: dict[str, Any]) -> str:
        frames = trade.get("mtf_timeframes") or {}
        alignment = trade.get("mtf_alignment") or "—"
        return "\n".join(
            (
                "Weeg | فتح صفقة ورقية",
                f"العملة: {trade.get('symbol', '—')}",
                f"الاتجاه: {trade.get('direction', '—')}",
                f"الإطار: {trade.get('timeframe', '—')}",
                f"الدخول: {trade.get('entry', '—')}",
                f"وقف الخسارة: {trade.get('stop_loss', '—')}",
                f"TP1: {trade.get('take_profit_1', '—')}",
                f"TP2: {trade.get('take_profit_2', '—')}",
                f"RR: {trade.get('risk_reward', '—')} | الثقة: {trade.get('confidence', '—')}%",
                f"MTF: {alignment} | 4h={frames.get('4h', {}).get('signal', '—')} | 1h={frames.get('1h', {}).get('signal', '—')} | 15m={frames.get('15m', {}).get('signal', '—')}",
                f"الوقت: {trade.get('created_at') or trade.get('signal_time') or '—'}",
            )
        )

    @staticmethod
    def format_trade_closed(trade: dict[str, Any]) -> str:
        result = trade.get("result") or ("WIN" if trade.get("status") == "CLOSED" else "LOSS")
        return "\n".join(
            (
                "Weeg | إغلاق صفقة ورقية",
                f"العملة: {trade.get('symbol', '—')}",
                f"الاتجاه: {trade.get('direction', '—')}",
                f"السبب: {trade.get('exit_reason', '—')}",
                f"الدخول: {trade.get('entry', '—')}",
                f"سعر الخروج: {trade.get('exit_price', '—')}",
                f"النتيجة: {result}",
                f"PnL: {trade.get('pnl', '—')}%",
                f"وقت الخروج: {trade.get('closed_at', '—')}",
            )
        )

    async def trade_opened(self, trade: dict[str, Any]) -> dict[str, int]:
        return await self.send_message(self.format_trade_opened(trade))

    async def trade_closed(self, trade: dict[str, Any]) -> dict[str, int]:
        return await self.send_message(self.format_trade_closed(trade))


class TelegramBotController:
    """Inline Telegram controls backed by the same in-process market and storage state."""

    def __init__(self, notifier: TelegramNotifier, store: Any, market: Any, settings: Any, cycle_state: dict[str, Any]):
        self.notifier = notifier
        self.store = store
        self.market = market
        self.settings = settings
        self.cycle_state = cycle_state

    @property
    def configured(self) -> bool:
        return self.notifier.configured

    def is_authorized(self, chat_id: Any) -> bool:
        return self.configured and str(chat_id) == str(self.notifier.chat_id)

    @staticmethod
    def menu_markup() -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "الصفقات المفتوحة", "callback_data": "open_trades"},
                    {"text": "الصفقات المغلقة", "callback_data": "closed_trades"},
                ],
                [
                    {"text": "الأسعار الحالية", "callback_data": "prices"},
                    {"text": "Summary Cycle", "callback_data": "cycle"},
                ],
                [{"text": "أداء النظام", "callback_data": "performance"}],
                [{"text": "القائمة الرئيسية", "callback_data": "menu"}],
            ]
        }

    @staticmethod
    def _number(value: Any, digits: int = 4) -> str:
        if value is None:
            return "—"
        try:
            return f"{float(value):,.{digits}f}"
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def _price(cls, value: Any) -> str:
        try:
            number = abs(float(value))
        except (TypeError, ValueError):
            return "—"
        digits = 2 if number >= 100 else 4 if number >= 1 else 6
        return cls._number(value, digits)

    @staticmethod
    def _utc(value: Any) -> str:
        if not value:
            return "—"
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return str(value).replace("T", " ")[:19]

    async def render_menu(self) -> str:
        return "Weeg | لوحة التحكم\n\nاختر البيانات التي تريد عرضها من الأزرار التالية.\nالتداول ورقي فقط، ولا توجد أوامر شراء أو بيع حقيقية."

    async def render_open_trades(self) -> str:
        trades = await self.store.list_active_trades()
        if not trades:
            return "Weeg | الصفقات المفتوحة\n\nلا توجد صفقات مفتوحة حاليًا."
        lines = [f"Weeg | الصفقات المفتوحة ({len(trades)})", ""]
        for trade in trades[:20]:
            symbol = trade.get("symbol", "—")
            current = self.market.tickers.get(str(symbol).upper(), {}).get("price")
            lines.extend(
                (
                    f"{symbol} | {trade.get('direction', '—')} | الحالة: {trade.get('status', '—')}",
                    f"الدخول: {self._price(trade.get('entry'))} | الحالي: {self._price(current)}",
                    f"SL: {self._price(trade.get('stop_loss'))} | TP1: {self._price(trade.get('take_profit_1'))}",
                    f"الفتح: {self._utc(trade.get('created_at') or trade.get('signal_time'))}",
                    "",
                )
            )
        return "\n".join(lines)

    async def render_closed_trades(self) -> str:
        trades = await self.store.list_trades("CLOSED_OR_STOPPED")
        if not trades:
            return "Weeg | الصفقات المغلقة\n\nلا توجد صفقات مغلقة حاليًا."
        lines = [f"Weeg | آخر الصفقات المغلقة ({len(trades)})", ""]
        for trade in trades[:15]:
            result = trade.get("result") or ("WIN" if trade.get("status") == "CLOSED" else "LOSS")
            lines.extend(
                (
                    f"{trade.get('symbol', '—')} | {trade.get('direction', '—')} | {result}",
                    f"السبب: {trade.get('exit_reason', '—')} | PnL: {self._number(trade.get('pnl'), 4)}%",
                    f"الخروج: {self._price(trade.get('exit_price'))} | الوقت: {self._utc(trade.get('closed_at'))}",
                    "",
                )
            )
        return "\n".join(lines)

    def render_prices(self) -> str:
        lines = ["Weeg | الأسعار الحالية", ""]
        for symbol in self.settings.symbol_list:
            ticker = self.market.tickers.get(symbol, {})
            if not ticker:
                lines.append(f"{symbol}: لا توجد بيانات بعد")
                continue
            change = ticker.get("change")
            change_text = "—" if change is None else f"{float(change):+.2f}%"
            lines.append(f"{symbol}: {self._price(ticker.get('price'))} ({change_text})")
        return "\n".join(lines)

    def render_cycle(self) -> str:
        state = self.cycle_state
        next_run = self._utc(state.get("next_run_at"))
        return "\n".join(
            (
                "Weeg | Summary Cycle",
                "",
                f"الحالة: {state.get('status', '—')}",
                f"الدورات المكتملة: {state.get('completed_cycles', 0)}",
                f"العملات المفحوصة: {state.get('scanned_symbols', 0)}",
                f"الإشارات الجاهزة: {state.get('ready_signals', 0)}",
                f"الصفقات المحفوظة: {state.get('saved_trades', 0)}",
                f"آخر العملات المحفوظة: {', '.join(state.get('last_saved_symbols') or []) or '—'}",
                f"الدورة التالية: {next_run}",
                f"آخر خطأ: {state.get('last_error') or 'لا يوجد'}",
            )
        )

    async def render_performance(self) -> str:
        closed = await self.store.list_trades("CLOSED_OR_STOPPED")
        open_trades = await self.store.list_active_trades()
        wins = sum(1 for trade in closed if (trade.get("result") or ("WIN" if trade.get("status") == "CLOSED" else "LOSS")) == "WIN")
        losses = sum(1 for trade in closed if (trade.get("result") or ("WIN" if trade.get("status") == "CLOSED" else "LOSS")) == "LOSS")
        total_pnl = sum(float(trade.get("pnl") or 0) for trade in closed)
        win_rate = (wins / len(closed) * 100) if closed else 0
        return "\n".join(
            (
                "Weeg | أداء النظام",
                "",
                f"الصفقات المفتوحة: {len(open_trades)}",
                f"الصفقات المغلقة: {len(closed)}",
                f"الفوز: {wins} | الخسارة: {losses}",
                f"نسبة الفوز: {win_rate:.2f}%",
                f"إجمالي PnL المسجل: {total_pnl:.4f}%",
                f"التخزين: {self.store.backend_name}",
                f"التغذية الحية: {'مفعّلة' if self.market.health_snapshot().get('live_feed') else 'متوقفة'}",
                f"الفحص الآلي: {'مفعّل' if self.store.has_persistent_storage else 'متوقف'}",
            )
        )

    async def render_callback(self, callback_data: str) -> str:
        try:
            if callback_data == "open_trades":
                return await self.render_open_trades()
            if callback_data == "closed_trades":
                return await self.render_closed_trades()
            if callback_data == "prices":
                return self.render_prices()
            if callback_data == "cycle":
                return self.render_cycle()
            if callback_data == "performance":
                return await self.render_performance()
            return await self.render_menu()
        except Exception as exc:
            log.warning("telegram control query failed: %s", type(exc).__name__)
            return "Weeg | تعذر جلب البيانات مؤقتًا\n\nحاول الضغط على الزر مرة أخرى."

    async def handle_update(self, update: dict[str, Any]) -> None:
        message = update.get("message") or {}
        callback = update.get("callback_query") or {}
        source = message or callback.get("message") or {}
        chat_id = (source.get("chat") or {}).get("id")
        if not self.is_authorized(chat_id):
            return
        if callback:
            await self.notifier.answer_callback_query(str(callback.get("id", "")))
            message_id = (callback.get("message") or {}).get("message_id")
            if message_id:
                text = await self.render_callback(str(callback.get("data") or "menu"))
                await self.notifier.edit_message(str(chat_id), int(message_id), text, self.menu_markup())
            return
        text = str(message.get("text") or "").strip()
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text else ""
        if command in {"/start", "/menu"}:
            await self.notifier.send_message(await self.render_menu(), self.menu_markup(), str(chat_id))

    async def run(self) -> None:
        if not self.configured:
            return
        offset: int | None = None
        while True:
            try:
                updates = await self.notifier.get_updates(offset=offset, timeout=25)
                for update in updates:
                    update_id = update.get("update_id")
                    if isinstance(update_id, int):
                        offset = update_id + 1
                    await self.handle_update(update)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("telegram controls polling failed: %s", type(exc).__name__)
                await asyncio.sleep(5)
