from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

from app.storage.store import Store

log = logging.getLogger("weeg.push")


def _vapid_public_key(private_key_pem: str | None) -> str | None:
    if not private_key_pem:
        return None
    try:
        from cryptography.hazmat.primitives import serialization

        key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        public = key.public_key().public_numbers()
        raw = b"\x04" + public.x.to_bytes(32, "big") + public.y.to_bytes(32, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    except Exception as exc:
        log.error("invalid VAPID private key: %s", exc)
        return None


class PushNotifier:
    def __init__(self, store: Store, private_key: str | None, subject: str | None):
        self.store = store
        self.private_key = private_key.replace("\\n", "\n") if private_key else None
        self.subject = subject or "mailto:admin@example.com"
        self.public_key = _vapid_public_key(self.private_key)

    @property
    def configured(self) -> bool:
        return bool(self.private_key and self.public_key)

    async def send(self, title: str, body: str, *, tag: str, data: dict[str, Any] | None = None) -> dict[str, int]:
        if not self.configured:
            return {"sent": 0, "failed": 0, "removed": 0}
        subscriptions = await self.store.list_push_subscriptions()
        sent = failed = removed = 0
        payload = json.dumps({
            "title": title,
            "body": body,
            "tag": tag,
            "data": data or {},
            "url": "/",
        }, ensure_ascii=False)
        for subscription in subscriptions:
            try:
                await asyncio.to_thread(self._send_one, subscription, payload)
                sent += 1
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in (404, 410):
                    try:
                        await self.store.delete_push_subscription(subscription["endpoint"])
                        removed += 1
                    except Exception:
                        log.exception("failed to remove expired push subscription")
                else:
                    failed += 1
                    log.warning("push delivery failed: %s", type(exc).__name__)
        return {"sent": sent, "failed": failed, "removed": removed}

    def _send_one(self, subscription: dict[str, Any], payload: str) -> None:
        from pywebpush import webpush

        webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                "keys": {
                    "p256dh": subscription["p256dh"],
                    "auth": subscription["auth"],
                },
            },
            data=payload,
            vapid_private_key=self.private_key,
            vapid_claims={"sub": self.subject},
            ttl=3600,
        )

    async def trade_opened(self, trade: dict[str, Any]) -> dict[str, int]:
        direction = trade.get("direction", "TRADE")
        symbol = trade.get("symbol", "")
        body = (
            f"{symbol} · دخول {trade.get('entry')} · وقف {trade.get('stop_loss')} · "
            f"هدف {trade.get('take_profit_1')} · ثقة {trade.get('confidence')}%"
        )
        return await self.send(
            f"Weeg — فتح صفقة {direction}",
            body,
            tag=f"trade-open-{trade.get('id', symbol)}",
            data={"event": "trade_opened", "trade_id": trade.get("id"), "symbol": symbol},
        )

    async def trade_closed(self, trade: dict[str, Any]) -> dict[str, int]:
        result = trade.get("result") or ("WIN" if trade.get("status") == "CLOSED" else "LOSS")
        label = "رابحة" if result == "WIN" else "خاسرة"
        body = (
            f"{trade.get('symbol', '')} · سعر الخروج {trade.get('exit_price')} · "
            f"النتيجة {result} · PnL {trade.get('pnl')}% · السبب {trade.get('exit_reason')}"
        )
        return await self.send(
            f"Weeg — إغلاق صفقة {label}",
            body,
            tag=f"trade-close-{trade.get('id', trade.get('symbol', 'trade'))}",
            data={"event": "trade_closed", "trade_id": trade.get("id"), "symbol": trade.get("symbol")},
        )
