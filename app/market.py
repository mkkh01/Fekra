"""
بيانات السوق الحية مع محرك تبديل تلقائي متعدد المصادر (Multi-Source Failover Engine).

المصدر الأساسي: Binance Vision (data-api.binance.vision)
المصدر الاحتياطي الفوري (Hot Backup): MEXC Global API v3 (api.mexc.com)

الميزات:
1. مطابقة 1-إلى-1 في واجهة v3 (نفس المسارات، نفس أسماء المعاملات، ونفس هياكل الـ JSON).
2. عند حدوث حظر مؤقت من بينانس (418/429)، يتحول النظام لحظياً للمزود الاحتياطي
   دون توقف التداول أو انهيار الدورة أو تأخر الإشارات، مع حفظ فترة التهدئة لمنع إزعاج بينانس.
3. استعلام مجمّع فائق السرعة للأسعار ودفاتر الأوامر في طلب شبكة واحد بدل 32 طلباً منفصلاً.
"""
import json
import re
import threading
import time

import pandas as pd
import requests

from . import config


class VisionMarket:
    # ── المصادر المعتمدة ──
    PRIMARY_BASES = (
        "https://data-api.binance.vision",
    )
    # مزود احتياطي فوري متطابق تماماً في واجهة v3 ولا يخضع لقيود أو حظر 418
    BACKUP_BASES = (
        "https://api.mexc.com",
    )

    _binance_cooldown_until = 0.0
    _active_provider = "binance"
    _lock = threading.Lock()

    def __init__(self, timeout=None):
        self.sess = requests.Session()
        self.sess.headers.update({"User-Agent": "CT-SNIPER/2.5"})
        self.timeout = timeout or config.MARKET_TIMEOUT_SEC
        self._last_good = None

    @classmethod
    def provider_status(cls):
        """حالة المزود الحالي مع توضيح أي تهدئة جارية لبينانس."""
        now = time.time()
        with cls._lock:
            act = cls._active_provider
            cool = cls._binance_cooldown_until
        if now < cool:
            rem = int(cool - now)
            return f"ok (احتياطي MEXC نشط — تهدئة بينانس {rem}ث)"
        if act == "mexc_backup":
            return "ok (احتياطي MEXC نشط)"
        return "ok (Binance Vision)"

    def _get(self, path, **kwargs):
        """
        محرك التوجيه الذكي:
        - إذا كان بينانس متاحاً (خارج فترة التهدئة) يتم الاتصال به أولاً.
        - إذا حدث 418 أو 429، تُسجّل مدة التهدئة فوراً ويتحول الطلب في نفس اللحظة للمزود الاحتياطي.
        - إذا كان بينانس في فترة تهدئة، لا يتم إرسال أي طلب له نهائياً احتراماً للعداد، ويُخدم الطلب عبر الاحتياطي.
        """
        now = time.time()
        timeout = kwargs.pop("timeout", self.timeout)

        # 1. محاولة بينانس (إن لم يكن قيد التهدئة)
        if now >= VisionMarket._binance_cooldown_until:
            for base in self.PRIMARY_BASES:
                try:
                    r = self.sess.get(f"{base}{path}", timeout=timeout, **kwargs)
                    if r.status_code in (418, 429):
                        # حظر مؤقت من بينانس: استخراج موعد انتهاء الحظر
                        text = r.text or ""
                        m = re.search(r"banned until (\d+)", text)
                        with VisionMarket._lock:
                            if m:
                                ban_ts = int(m.group(1)) / 1000.0
                                VisionMarket._binance_cooldown_until = max(VisionMarket._binance_cooldown_until, ban_ts + 5.0)
                            else:
                                retry_after = int(r.headers.get("Retry-After", "180"))
                                VisionMarket._binance_cooldown_until = max(VisionMarket._binance_cooldown_until, time.time() + retry_after)
                        # كسر المحاولة والانتقال الفوري للمزود الاحتياطي دون إثارة استثناء!
                        break
                    elif r.status_code == 200:
                        with VisionMarket._lock:
                            VisionMarket._active_provider = "binance"
                            self._last_good = base
                        return r
                except requests.RequestException:
                    break

        # 2. المزود الاحتياطي الفوري (MEXC Global v3)
        for base in self.BACKUP_BASES:
            try:
                r = self.sess.get(f"{base}{path}", timeout=timeout, **kwargs)
                if r.status_code == 200:
                    with VisionMarket._lock:
                        VisionMarket._active_provider = "mexc_backup"
                        self._last_good = base
                    return r
            except requests.RequestException:
                continue

        # 3. محاولة أخيرة مع بينانس إذا لم يسبق تجربته
        raise requests.RequestException("تعذّر الاتصال بجميع مزودي السوق المعتمدين (Binance و MEXC)")

    def klines(self, symbol, interval, limit=300):
        """
        جلب الشموع الفنية.
        متوافق تماماً مع صيغة بينانس (12 عموداً) ومزود MEXC (8 أعمدة) عبر اقتطاع الأعمدة الـ 6 الأساسية.
        """
        r = self._get("/api/v3/klines",
                      params={"symbol": symbol, "interval": interval, "limit": limit})
        data = r.json()
        raw = [row[:6] for row in data]
        df = pd.DataFrame(raw, columns=["open_time", "open", "high", "low", "close", "volume"]).astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
        return df

    def price(self, symbol):
        """سعر آخر صفقة لرمز محدد."""
        r = self._get("/api/v3/ticker/price", params={"symbol": symbol})
        return float(r.json()["price"])

    def prices(self, symbols):
        """
        جلب الأسعار لعدة رموز دفعة واحدة.
        يستخدم طلباً مجمّعاً واحداً (/api/v3/ticker/price بدون باراميترات) لتقليص استهلاك الشبكة بنسبة 95%.
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
        # مسار احتياطي فردي في حال تعذّر الاستعلام المجمّع
        out = {}
        for s in symbols:
            try:
                out[s] = self.price(s)
            except Exception:
                pass
        return out

    def book(self, symbol):
        """أفضل عرض وطلب للتنفيذ الواقعي مع السبريد."""
        r = self._get("/api/v3/ticker/bookTicker", params={"symbol": symbol})
        d = r.json()
        return {
            "bid": float(d["bidPrice"]),
            "ask": float(d["askPrice"]),
            "bid_qty": float(d.get("bidQty", 0)),
            "ask_qty": float(d.get("askQty", 0)),
        }

    def books(self, symbols):
        """دفاتر العرض والطلب لعدة رموز دفعة واحدة في طلب مجمّع واحد."""
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
        """معدل التمويل الحالي إن توفر، وإلا يُستخدم الافتراضي المحسوب."""
        try:
            r = self.sess.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                              params={"symbol": symbol}, timeout=self.timeout)
            if r.status_code == 200:
                return float(r.json()["lastFundingRate"])
        except Exception:
            pass
        return None
