from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


TradingMode = Literal["OBSERVE", "PAPER", "LIVE_GUARDED", "EMERGENCY_STOP"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Fekra Trading Brain"
    app_env: str = "development"
    log_level: str = "INFO"
    trading_mode: TradingMode = "PAPER"

    supabase_url: str = ""
    supabase_key: str = ""
    redis_url: str = ""

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.7-flash"
    gemini_api_key_1: str = ""
    gemini_api_key_2: str = ""
    gemini_api_key_3: str = ""
    gemini_api_key_4: str = ""
    gemini_api_key_5: str = ""

    cors_origins: str = "http://localhost:8000"
    jwt_secret: str = ""
    news_poll_seconds: int = Field(default=300, ge=60, le=3600)
    market_reconnect_seconds: int = Field(default=5, ge=1, le=120)

    rss_feeds: str = (
        "https://www.cryptobriefing.com/feed/;"
        "https://www.ccn.com/news/crypto-news/feeds/;"
        "https://cointelegraph.com/rss;"
        "https://www.coindesk.com/arc/outboundfeeds/rss/;"
        "https://decrypt.co/feed"
    )

    @field_validator("trading_mode", mode="before")
    @classmethod
    def normalize_trading_mode(cls, value: str) -> str:
        return str(value or "PAPER").upper()

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def rss_feed_list(self) -> list[str]:
        return [item.strip() for item in self.rss_feeds.split(";") if item.strip()]

    @property
    def gemini_keys(self) -> list[str]:
        numbered = [
            self.gemini_api_key_1,
            self.gemini_api_key_2,
            self.gemini_api_key_3,
            self.gemini_api_key_4,
            self.gemini_api_key_5,
        ]
        candidates = numbered if any(value.strip() for value in numbered) else [self.gemini_api_key]
        unique: list[str] = []
        for key in candidates:
            value = key.strip()
            if value and value not in unique:
                unique.append(value)
        return unique[:5]

    @property
    def paper_only(self) -> bool:
        return self.trading_mode in {"OBSERVE", "PAPER"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
