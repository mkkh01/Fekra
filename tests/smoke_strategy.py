"""اختبار دخان مؤقت لمنطق الاستراتيجية — يعمل بدون إنترنت أو قاعدة بيانات."""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import strategy as st, config


def make_df(n, seed=7, drift=0.0004, vol=0.012):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
    vol_ = rng.uniform(1000, 5000, n)
    return pd.DataFrame({
        "open_time": pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close, "volume": vol_,
    })


def test_indicators_finite():
    df = make_df(300)
    assert np.isfinite(st.atr(df).iloc[-1])
    assert 0 <= st.rsi(df["close"], 7).iloc[-1] <= 100
    assert np.isfinite(st.adx(df).iloc[-1])
    print("✔ indicators finite")


def test_day_scan_returns_shape():
    df = make_df(300)
    h1c, h1e = st.h1_now(df)
    anc = make_df(130, seed=11)
    a = st.anchor_ar_now(anc)
    side, px, atr_, dbg = st.day_scan_debug(df, a, h1c, h1e, config.DAY)
    assert side in (-1, 0, 1)
    assert dbg["reject"] is None or isinstance(dbg["reject"], str)
    print(f"✔ day_scan side={side} reject={dbg['reject']} trig={dbg.get('trig')}")


def test_falcon_scan_returns_shape():
    df = make_df(260)
    dd = make_df(250, seed=3)
    dc, e50, e200 = st.daily_regime(dd)
    side, px, atr_, dbg = st.falcon_scan_debug(df, dc, e50, e200, config.FALCON)
    assert side in (0, 1)
    print(f"✔ falcon_scan side={side} reject={dbg['reject']} trig={dbg.get('trig')}")


def test_warmup_guards():
    short = make_df(50)
    h1c, h1e = st.h1_now(short)
    assert (h1c, h1e) == (None, None), "h1_now يجب أن يرجع None عند قلة البيانات"
    s, _, _, d = st.day_scan_debug(short, 0.0, h1c, h1e, config.DAY)
    assert s == 0 and d["reject"] == "warmup", d
    # وبيانات كافية الشموع لكن بلا فلتر الساعة → يجب أن يرفض بـ h1-missing
    big = make_df(300)
    s, _, _, d = st.day_scan_debug(big, 0.0, None, None, config.DAY)
    assert s == 0 and d["reject"] == "h1-missing", d
    print("✔ warmup guards")


def test_h1_length_is_tight():
    """كم عدد الشموع الساعبة المتاحة فعلياً من 300 شمعة 15m بعد إسقاط المتشكلة؟"""
    df = make_df(300).iloc[:-1]
    h1 = df.set_index("open_time")["close"].resample("1h").last().dropna()
    print(f"· شموع ساعية من 299×15m: {len(h1)} (الشرط >= 60)")
    assert len(h1) >= 60


if __name__ == "__main__":
    for fn in [test_indicators_finite, test_day_scan_returns_shape,
               test_falcon_scan_returns_shape, test_warmup_guards,
               test_h1_length_is_tight]:
        fn()
    print("\nكل اختبارات الدخان نجحت ✅")
