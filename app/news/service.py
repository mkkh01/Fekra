from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.config.settings import get_settings
from app.state import RuntimeState
from app.storage.store import StorageManager

logger = logging.getLogger(__name__)

SYMBOL_ALIASES = {
    "BTC": "BTCUSDT", "BITCOIN": "BTCUSDT",
    "ETH": "ETHUSDT", "ETHEREUM": "ETHUSDT",
    "BNB": "BNBUSDT", "SOL": "SOLUSDT", "SOLANA": "SOLUSDT",
    "XRP": "XRPUSDT", "DOGE": "DOGEUSDT", "ADA": "ADAUSDT",
    "AVAX": "AVAXUSDT", "LINK": "LINKUSDT", "DOT": "DOTUSDT",
    "TRX": "TRXUSDT", "LTC": "LTCUSDT", "BCH": "BCHUSDT",
    "SUI": "SUIUSDT", "NEAR": "NEARUSDT", "APT": "APTUSDT",
    "TON": "TONUSDT", "XLM": "XLMUSDT", "HBAR": "HBARUSDT",
    "ICP": "ICPUSDT",
}


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def parse_time(value: str | None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return value


class NewsService:
    def __init__(self, state: RuntimeState, storage: StorageManager | None = None) -> None:
        self.state = state
        self.storage = storage
        self.task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.seen: set[str] = set()

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run(), name="free-rss-news")

    async def stop(self) -> None:
        self._stop.set()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        await self.poll_once()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=get_settings().news_poll_seconds)
            except asyncio.TimeoutError:
                await self.poll_once()

    async def poll_once(self) -> None:
        feeds = get_settings().rss_feed_list
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": "FekraTradingBrain/1.0 RSS reader"}) as client:
            results = await asyncio.gather(*(self._fetch_feed(client, url) for url in feeds), return_exceptions=True)
        count = 0
        for feed_url, result in zip(feeds, results):
            if isinstance(result, Exception):
                self.state.event("NEWS", "RSS feed unavailable", {"feed": feed_url, "error": str(result)})
                continue
            for item in result:
                fingerprint = item["id"]
                if fingerprint in self.seen:
                    continue
                self.seen.add(fingerprint)
                self.state.add_news(item)
                if self.storage is not None:
                    await self.storage.write_news(item)
                count += 1
        if count:
            self.state.event("NEWS", "New free RSS news ingested", {"count": count})

    async def _fetch_feed(self, client: httpx.AsyncClient, feed_url: str) -> list[dict[str, Any]]:
        response = await client.get(feed_url)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else root.findall(".//item")
        normalized: list[dict[str, Any]] = []
        for item in items[:50]:
            title = clean_text(self._text(item, "title"))
            link = clean_text(self._text(item, "link"))
            description = clean_text(self._text(item, "description"))[:1000]
            published = self._text(item, "pubDate") or self._text(item, "published")
            if not title or not link:
                continue
            symbols = self._symbols_in(f"{title} {description}")
            fingerprint = hashlib.sha256(f"{title}|{link}".encode()).hexdigest()
            normalized.append({
                "id": fingerprint,
                "title": title,
                "url": link,
                "summary": description,
                "source": feed_url,
                "published_at": parse_time(published),
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "symbols": symbols,
                "evidence_status": "sourced",
            })
        return normalized

    @staticmethod
    def _text(item: ET.Element, name: str) -> str | None:
        node = item.find(name)
        if node is None:
            node = item.find(f"{{*}}{name}")
        return node.text if node is not None else None

    @staticmethod
    def _symbols_in(text: str) -> list[str]:
        upper = text.upper()
        return sorted({symbol for alias, symbol in SYMBOL_ALIASES.items() if re.search(rf"\b{re.escape(alias)}\b", upper)})
