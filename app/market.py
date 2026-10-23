"""
بيانات السوق الرسمية من منظومة Binance العالمية الحصرية (Binance Only).

الميزات والضمانات الفنية:
1. اتصال حصري بخوادم بينانس الرسمية (data-api.binance.vision و api1..4.binance.com).
2. استعلام مجمّع فائق السرعة عبر طلب واحد فقط (Single-Request Batch) للأسعار ودفاتر الأوامر
   (وزن 4 فقط في الدقيقة لجميع العملات مجتمعة بدل 64 طلباً منفصلاً).
3. دعم كامل للمفاتيح الرسمية (X-MBX-APIKEY) لمنح الحساب أولوية وتفادي قيود Cloudflare WAF.
4. تدوير تلقائي بين خوادم بينانس الرسمية عند أي اختناق في نقطة وصول معينة.
5. حماية التهدئة التلقائية لحساب مدة الحظر بدقة ومنع تمديده عند حدوث أي تقييد مؤقت.
"""
import json
import re
import threading
import time

import pandas as pd
import requests

from . import config


class VisionMarket:
    # ── خوادم بينانس الرسمية العالمية المعتمدة ──
    PRIMARY_BASES = (
        "https://data-api.binance.vision",
        "https://api1.binance.com",
        "https://api2.binance.com",
        "https://api3.binance.com",
        "https://api.binance.com",
    )

    _cooldown_until = 0.0
    _active_base = "https://data-api.binance.vision"
    _lock = threading.Lock()

    def __init__(self, timeout=None):
        self.sess = requests.Session()
        headers = {"User-Agent": "CT-BINANCE-SNIPER/3.0"}
        if getattr(config, "BINANCE_API_KEY", ""):
            headers["X-MBX-APIKEY"] = config.BINANCE_API_KEY.strip()
        self.sess.headers.update(headers)
        self.timeout = timeout or config.MARKET_TIMEOUT_SEC

    @classmethod
    def provider_status(cls):
        """حالة خوادم بينانس مع بيان التهدئة إن وجدت."""
        now = time.time()
        with cls._lock:
            cool = cls._cooldown_until
            base = cls._active_base
        if now < cool:
            rem = int(cool - now)
            return f"cooldown ({rem}s remaining) — ننتظر انتهاء فترة التهدئة لبينانس"
        name = "Vision" if "vision" in base else "Global API"
        return f"ok (Binance {name})"

    def _get(self, path, **kwargs):
        """
        إرسال الطلب لخوادم بينانس مع التدوير الذكي بين النطاقات الرسمية.
        """
        now = time.time()
        timeout = kwargs.pop("timeout", self.timeout)

        if now < VisionMarket._cooldown_until:
            rem = int(VisionMarket._cooldown_until - now)
            raise requests.HTTPError(f"418 Cooldown Active ({rem}s remaining) — ننتظر انتهاء فترة التهدئة لرفع حظر بينانس تلقائياً")

        last_err = None
        for base in self.PRIMARY_BASES:
            try:
                r = self.sess.get(f"{base}{path}", timeout=timeout, **kwargs)
                if r.status_code in (418, 429):
                    # استخراج موعد انتهاء الحظر بدقة
                    text = r.text or ""
                    m = re.search(r"banned until (\d+)", text)
                    with VisionMarket._lock:
                        if m:
                            ban_ts = int(m.group(1)) / 1000.0
                            VisionMarket._cooldown_until = max(VisionMarket._cooldown_until, ban_ts + 5.0)
                        else:
                            retry_after = int(r.headers.get("Retry-After", "120"))
                            VisionMarket._cooldown_until = max(VisionMarket._cooldown_until, time.time() + retry_after)
                    last_err = requests.HTTPError(f"418 IP Rate Limit from {base}")
                    continue  # محاولة نطاق رسمي آخر من بينانس
                if r.status_code == 200:
                    with VisionMarket._lock:
                        VisionMarket._active_base = base
                    return r
                last_err = requests.HTTPError(f"{r.status_code} from {base}")
            except requests.RequestException as e:
                last_err = e
                continue

        if now < VisionMarket._cooldown_until:
            rem = int(VisionMarket._cooldown_until - now)
            raise requests.HTTPError(f"418 Cooldown Active ({rem}s remaining) — ننتظر انتهاء فترة التهدئة لرفع حظر بينانس تلقائياً")
        raise last_err or requests.RequestException("خوادم بينانس الرسمية غير متاحة حالياً")

    def klines(self, symbol, interval, limit=300):
        """جلب شموع الأسعار من بينانس بصيغتها الأصلية المعتمدة."""
        r = self._get("/api/v3/klines",
                      params={"symbol": symbol, "interval": interval, "limit": limit})
        data = r.json()
        raw = [row[:6] for row in data]
        df = pd.DataFrame(raw, columns=["open_time", "open", "high", "low", "close", "volume"]).astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
        return df

    def price(self, symbol):
        """سعر السوق اللحظي المباشر لرمز محدد من بينانس."""
        r = self._get("/api/v3/ticker/price", params={"symbol": symbol})
        return float(r.json()["price"])

    def prices(self, symbols):
        """
        جلب أسعار جميع العملات دفعة واحدة في طلب واحد فقط (Batch Ticker)
        بدون إرسال 32 طلباً منفرداً — مما يستهلك وزن 4 فقط من أصل 1200 المسموحة في الدقيقة!
        """
        syms_set = set(symbols)
        try:
            r = self._get("/api/v3/ticker/price")
            data = r.json()
            if isinstance(data, list):
                return {x["symbol"]: float(x["price"]) for x in data if x.get("symbol") in syms_set}
            elif isinstance(data, dict) and "price" in data:
                return {data.get("symbol", ""): float(data["price"])}
        except Exception:
            pass
        # مسار احتياطي فردي
        out = {}
        for s in symbols:
            try:
                out[s] = self.price(s)
            except Exception:
                pass
        return out

    def book(self, symbol):
        """أفضل أسعار العرض والطلب (Bid/Ask) من دفتر أوامر بينانس الحي."""
        r = self._get("/api/v3/ticker/bookTicker", params={"symbol": symbol})
        d = r.json()
        return {
            "bid": float(d["bidPrice"]),
            "ask": float(d["askPrice"]),
            "bid_qty": float(d.get("bidQty", 0)),
            "ask_qty": float(d.get("askQty", 0)),
        }

    def books(self, symbols):
        """دفاتر أوامر جميع الرموز دفعة واحدة في طلب واحد فقط من بينانس."""
        syms_set = set(symbols)
        try:
            r = self._get("/api/v3/ticker/bookTicker")
            data = r.json()
            if isinstance(data, list):
                return {
                    x["symbol"]: {
                        "bid": float(x["bidPrice"]),
                        "ask": float(x["askPrice"]),
                        "bid_qty": float(x.get("bidQty", 0)),
                        "ask_qty": float(x.get("askQty", 0)),
                    }
                    for x in data
                    if x.get("symbol") in syms_set
                }
        except Exception:
            pass
        return {s: self.book(s) for s in symbols}

    def funding_rate(self, symbol):
        """معدل التمويل الحالي إن توفر من خوادم بينانس للعقود."""
        try:
            r = self.sess.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                              params={"symbol": symbol}, timeout=self.timeout)
            if r.status_code == 200:
                return float(r.json()["lastFundingRate"])
        except Exception:
            pass
        return None
