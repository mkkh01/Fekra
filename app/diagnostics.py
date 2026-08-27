"""Safe structured diagnostics for production logs.

Never pass tokens, API keys, passwords, request payloads, or full user data to
these helpers. Values are intentionally flattened and bounded for Render logs.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

_SECRET_NAMES = (
    "TELEGRAM_BOT_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_ANON_KEY",
    "SUPABASE_KEY",
    "DATABASE_URL",
    "SUPABASE_DB_URL",
    "REDIS_URL",
)


def configure_logging() -> None:
    level_name = str(os.getenv("LOG_LEVEL", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )


def safe_text(value: Any, limit: int = 240) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ")
    for name in _SECRET_NAMES:
        secret = os.getenv(name)
        if secret:
            text = text.replace(secret, "<redacted>")
    text = re.sub(r"(token|secret|password|api[_-]?key)=([^&\s]+)", r"\1=<redacted>", text, flags=re.I)
    return text[:limit]


def emit(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    parts = [f"event={safe_text(event, 80)}"]
    for key, value in fields.items():
        if value is None:
            continue
        parts.append(f"{key}={safe_text(value)}")
    logger.log(level, "DIAG %s", " ".join(parts))


def exception_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}:{safe_text(exc)}"
