"""بيانات السوق من Binance Vision العامة (بدون مفاتيح، بلا حظر جغرافي)."""
import json
import requests
import pandas as pd


class VisionMarket:
    BASES = ("https://api.binance.us", "https://api.binance.me",
             "https://api-gcp.binance.com", "https://www.binance.com",
             "https://api2.binance.com", "https://api3.binance.com",
             "https://api4.binance.com", "https://data-api.binance.vision",
             "https://api.binance.com", "https://api1.binance.com")

    def __init__(self):
        self.sess = requests.Session()
        self.sess.headers.update({"User-Agent": "CT-FALCON/1.0"})

    def _get(self, path, **kwargs):
        last = None
        for base in self.BASES:
            try:
                r = self.sess.get(f"{base}{path}", **kwargs)
                if r.status_code != 200:
                    last = requests.HTTPError(f"{r.status_code} from {base}")
                    continue
                return r
            except requests.RequestException as e:
                last = e
        raise last or requests.RequestException("Binance endpoints unavailable")

    def klines(self, symbol, interval, limit=300):
        r = self._get("/api/v3/klines",
                       params={"symbol": symbol, "interval": interval, "limit": limit},
                       timeout=20)
        df = pd.DataFrame(r.json(),
                          columns=["open_time", "open", "high", "low", "close", "volume",
                                   "ct", "qv", "tr", "tb", "tq", "ig"])
        df = df[["open_time", "open", "high", "low", "close", "volume"]].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
        return df

    def price(self, symbol):
        r = self._get("/api/v3/ticker/price", params={"symbol": symbol}, timeout=10)
        return float(r.json()["price"])

    def prices(self, symbols):
        """أسعار دفعة واحدة (مع fallback فردي)."""
        try:
            r = self._get("/api/v3/ticker/price",
                          params={"symbols": json.dumps(list(symbols))}, timeout=15)
            return {x["symbol"]: float(x["price"]) for x in r.json()}
        except Exception:
            out = {}
            for s in symbols:
                try:
                    out[s] = self.price(s)
                except Exception:
                    pass
            return out
