"""
بيانات السوق من Binance (نقاط البيانات العامة — بلا مفاتيح).

تغيّر مهم: أُزيلت api.binance.us من القائمة لأنها **بورصة منفصلة** بسيولة وأحجام
مختلفة عن Binance.com. خلط المصدرين يجعل الإشارة تعتمد على "من يجيب أولاً" بدل
السوق نفسه، ويفسد مطابقة النتائج مع الباك تست.
"""
import json
import threading

import pandas as pd
import requests

from . import config


class VisionMarket:
    # المصادر الأساسية الرسمية لبيانات Binance العالمية — بلا قيود جغرافية وبلا حظر
    PRIMARY_BASES = (
        "https://data-api.binance.vision",
    )
    # حُذفت api.binance.us نهائياً لأنها بورصة أمريكية منفصلة بأسعار منحرفة تختلف عن بينانس العالمية
    FALLBACK_BASES = ()

    _cooldown_until = 0

    def __init__(self, timeout=None):
        self.sess = requests.Session()
        self.sess.headers.update({"User-Agent": "CT-SNIPER/2.0"})
        self.timeout = timeout or config.MARKET_TIMEOUT_SEC
        self._last_good = None
        self._lock = threading.Lock()

    def _order(self):
        # نعتمد حصراً على data-api.binance.vision لضمان مطابقة الأسعار الحية مع بينانس العالمية بدقة تامة
        return self.PRIMARY_BASES

    def _get(self, path, **kwargs):
        import time, re
        last = None
        for base in self._order():
            try:
                r = self.sess.get(f"{base}{path}", **kwargs)
                if r.status_code in (418, 429):
                    # حظر IP مؤقت (418/429): استخراج موعد انتهاء الحظر بدقة
                    text = r.text or ""
                    m = re.search(r"banned until (\d+)", text)
                    if m:
                        ban_ts = int(m.group(1)) / 1000.0
                        VisionMarket._cooldown_until = max(VisionMarket._cooldown_until, ban_ts + 10.0)
                    else:
                        retry_after = int(r.headers.get("Retry-After", "300"))
                        VisionMarket._cooldown_until = max(VisionMarket._cooldown_until, time.time() + max(180, retry_after))
                    last = requests.HTTPError(f"{r.status_code} from {base}")
                    # استمر فوراً للمصدر الاحتياطي دون إيقاف الدورة!
                    continue
                if r.status_code != 200:
                    last = requests.HTTPError(f"{r.status_code} from {base}")
                    continue
                with self._lock:
                    self._last_good = base
                return r
            except requests.RequestException as e:
                last = e
        raise last or requests.RequestException("Binance endpoints unavailable")

    def klines(self, symbol, interval, limit=300):
        r = self._get("/api/v3/klines",
                      params={"symbol": symbol, "interval": interval, "limit": limit},
                      timeout=self.timeout)
        df = pd.DataFrame(r.json(),
                          columns=["open_time", "open", "high", "low", "close", "volume",
                                   "ct", "qv", "tr", "tb", "tq", "ig"])
        df = df[["open_time", "open", "high", "low", "close", "volume"]].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
        return df

    def price(self, symbol):
        r = self._get("/api/v3/ticker/price", params={"symbol": symbol},
                      timeout=self.timeout)
        return float(r.json()["price"])

    def prices(self, symbols):
        """أسعار دفعة واحدة (مع fallback فردي)."""
        try:
            r = self._get("/api/v3/ticker/price",
                          params={"symbols": json.dumps(list(symbols), separators=(",", ":"))},
                          timeout=self.timeout)
            return {x["symbol"]: float(x["price"]) for x in r.json()}
        except Exception:
            out = {}
            for s in symbols:
                try:
                    out[s] = self.price(s)
                except Exception:
                    pass
            return out

    def book(self, symbol):
        """
        أفضل عرض وطلب. الشراء يُنفَّذ على ask والبيع على bid — أي أنك تدفع السبريد.
        هذا هو السعر القابل للتنفيذ فعلاً، بخلاف `price()` الذي يعطي آخر صفقة فقط.
        """
        r = self._get("/api/v3/ticker/bookTicker", params={"symbol": symbol},
                      timeout=self.timeout)
        d = r.json()
        return {"bid": float(d["bidPrice"]), "ask": float(d["askPrice"]),
                "bid_qty": float(d["bidQty"]), "ask_qty": float(d["askQty"])}

    def books(self, symbols):
        """دفتر العرض/الطلب لعدة رموز دفعة واحدة."""
        try:
            r = self._get("/api/v3/ticker/bookTicker",
                          params={"symbols": json.dumps(list(symbols), separators=(",", ":"))},
                          timeout=self.timeout)
            return {x["symbol"]: {"bid": float(x["bidPrice"]), "ask": float(x["askPrice"]),
                                  "bid_qty": float(x["bidQty"]), "ask_qty": float(x["askQty"])}
                    for x in r.json()}
        except Exception:
            return {s: self.book(s) for s in symbols}

    def funding_rate(self, symbol):
        """
        معدل التمويل الحالي للعقود الدائمة (لكل 8 ساعات).
        قد لا يكون متاحاً حسب المنطقة الجغرافية — يُرجع None عند التعذّر
        فيُستخدم المعدل الافتراضي المتحفظ من الإعدادات.
        """
        try:
            r = self.sess.get("https://fapi.binance.com/fapi/v1/premiumIndex",
                              params={"symbol": symbol}, timeout=self.timeout)
            if r.status_code == 200:
                return float(r.json()["lastFundingRate"])
        except Exception:
            pass
        return None
