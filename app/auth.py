from __future__ import annotations

from typing import Any

import httpx
from fastapi import Header, HTTPException

from app.config import get_settings


async def require_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    settings = get_settings()
    token = (authorization or "").strip()
    if not token.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="يجب تسجيل الدخول أولًا")
    public_key = settings.supabase_public_key
    if not public_key or not settings.supabase_http_url:
        raise HTTPException(status_code=503, detail="مصادقة Supabase غير مهيأة في الخادم")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(
                f"{settings.supabase_http_url.rstrip('/')}/auth/v1/user",
                headers={"apikey": public_key, "Authorization": token},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="تعذر التحقق من جلسة المستخدم") from exc
    if response.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="جلسة المستخدم غير صالحة أو منتهية")
    if response.status_code >= 400:
        raise HTTPException(status_code=503, detail="تعذر التحقق من جلسة المستخدم")
    try:
        user = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="استجابة المصادقة غير صالحة") from exc
    if not isinstance(user, dict) or not user.get("id"):
        raise HTTPException(status_code=401, detail="لم يتم العثور على مستخدم صالح")
    return user
