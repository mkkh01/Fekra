"""
محاكي سريع: نفس منطق الإشارة حرفياً، لكن بمؤشرات محسوبة مرة واحدة.

كل المؤشرات المستخدمة (ATR, RSI, ADX, المتوسطات المتحركة) **سببية** — تعتمد على
الماضي فقط، لذا حسابها على السلسلة كاملة ثم أخذ العنصر i يعطي نفس نتيجة حسابها
على إطار فرعي ينتهي عند i. الاستثناء الوحيد هو إعادة تجميع الشموع الساعية/اليومية،
حيث الشمعة الأخيرة جزئية — وتُعالَج هنا بحساب تزايدي يطابق الأصل تماماً.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, strategy as st, exec as ex

ALPHA50 = 2.0 / (50 + 1.0)      # ewm(span=50, adjust=False)
ALPHA200 = 2.0 / (200 + 1.0)


# ───────────────────────── مؤشرات محسوبة مرة واحدة ─────────────────────────
def prep(df, vol_window):
    """يحسب كل المؤشرات السببية مرة واحدة على السلسلة كاملة."""
    c = df["close"].to_numpy(float)
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    v = df["volume"].to_numpy(float)
    return dict(
        c=c, o=o, h=h, l=l, v=v,
        a=st.atr(df).to_numpy(float),
        r7=st.rsi(df["close"], 7).to_numpy(float),
        dx=st.adx(df).to_numpy(float),
        vs=pd.Series(v).rolling(vol_window).mean().to_numpy(float),
        ot=df["open_time"],
    )


def prep_hourly(df):
    """
    يعيد (موضع الساعة, سلسلة الإغلاقات الساعوية, متوسطها المتحرك 50)
    لمحاكاة h1_now() بدقة دون إعادة الحساب في كل شمعة.
    """
    hrs = df["open_time"].dt.floor("1h")
    codes, uniq = pd.factorize(hrs, sort=True)      # ترتيب تصاعدي زمنياً
    # آخر إغلاق 15m داخل كل ساعة (باستخدام كل الشموع — صحيح للساعات المكتملة)
    last_close = pd.Series(df["close"].to_numpy()).groupby(codes).last()
    H = last_close.reindex(range(len(uniq))).ffill().to_numpy(float)
    e = st.ema(pd.Series(H), 50).to_numpy(float)     # EMA50 على الساعات المكتملة
    return codes, H, e


def h1_at(codes, e, i, close_i):
    """
    يطابق st.h1_now: الإغلاق الساعوي الجزئي = إغلاق الشمعة الحالية،
    والمتوسط = خطوة EMA واحدة إضافية فوق متوسط الساعات المكتملة السابقة.
    """
    k = codes[i]
    if k < 59:                       # len(h1) يجب أن يكون >= 60
        return None, None
    prev = e[k - 1] if k > 0 else close_i
    return float(close_i), float(ALPHA50 * close_i + (1 - ALPHA50) * prev)


# ───────────────────────── إشارة DAY (نسخة سريعة) ─────────────────────────
def day_signal(P, i, a_now, h1c, h1e, p):
    c, o, a, r7, dx, v, vs = P["c"][i], P["o"][i], P["a"][i], P["r7"][i], \
                             P["dx"][i], P["v"][i], P["vs"][i]
    if np.isnan([a, r7, dx, vs]).any():
        return 0, None, None, "nan"
    if not (v > p["vol_mult"] * vs):
        return 0, None, None, "vol"
    ap = a / c
    if not (p["atr_min_pct"] < ap < p["atr_max_pct"]):
        return 0, None, None, "atr-range"
    if dx > p["adx_max"]:
        return 0, None, None, "adx"
    if h1c is None or h1e is None:
        return 0, None, None, "h1-missing"
    ar = (c - o) / a
    anchor_ok = abs(a_now) < p["btc_cap"]
    if ar < -p["dev"] and anchor_ok and r7 < p["rsiX"] and h1c > h1e:
        return 1, float(c), float(a), None
    if ar > p["dev"] and anchor_ok and r7 > 100 - p["rsiX"] and h1c < h1e:
        return -1, float(c), float(a), None
    return 0, None, None, "no-trigger"


def run_day(df, btc_df, p, mode, equity=config.PAPER_EQUITY,
            fee_bps=config.FEE_BPS, worst_case=True):
    P = prep(df, 96)
    codes, H, e = prep_hourly(df)
    n = len(df)
    hold_h = p["max_hold_bars"] * 0.25
    # محاذاة المرساة (BTC) مع الرمز
    bidx = pd.Series(np.arange(len(btc_df), dtype=int), index=pd.Index(btc_df["open_time"]))
    apos = df["open_time"].map(bidx).fillna(-1).astype(int).to_numpy()

    # تسريع: قيمة مرساة BTC محسوبة مرة واحدة لكل شمعة بدل تقطيع الإطار في كل مرة
    btc_a = prep(btc_df, 96)["a"]
    btc_c = btc_df["close"].to_numpy(float)
    btc_o = btc_df["open"].to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        btc_ar = np.where((btc_a > 0) & np.isfinite(btc_a),
                          (btc_c - btc_o) / btc_a, 999.0)

    trades = []
    ot = P["ot"]
    for i in range(100, n - 2):
        j = apos[i]
        if j < 100:
            continue
        a_now = float(btc_ar[j])
        h1c, h1e = h1_at(codes, e, i, P["c"][i])
        side, px, atr, rej = day_signal(P, i, a_now, h1c, h1e, p)
        if side == 0 or atr is None:
            continue

        if mode == "old":
            entry = float(P["c"][i])                       # ❌ إغلاق شمعة الإشارة
            entry_ts = ot.iloc[i] + timedelta(minutes=15)
            start_i, start_ts = i, None                    # ❌ يبدأ من شمعة الدخول
        elif mode == "moc":
            # أمر "سعر الإغلاق": تُنفّذ عند إغلاق شمعة الإشارة، والخروج يُقيَّم
            # من الشمعة التالية فصاعداً — أقرب نموذج لما يفعله الباك تست الأصلي.
            entry = ex.apply_cost(float(P["c"][i]), side, ex.SLIPPAGE_SIM_BPS)
            entry_ts = ot.iloc[i] + timedelta(minutes=15)
            start_i, start_ts = i + 1, entry_ts
        else:
            entry = float(P["o"][i + 1])                   # ✅ أول سعر متاح
            entry = ex.apply_cost(entry, side, ex.SLIPPAGE_SIM_BPS)
            entry_ts = ot.iloc[i + 1]
            start_i, start_ts = i + 1, entry_ts            # ✅ بعد لحظة الدخول فقط

        tp, sl = ex.plan_exit(side, entry, atr, p["tp_atr"], p["sl_atr"])
        sl_d = abs(entry - sl) or p["sl_atr"] * atr
        qty = (equity * p["risk_per_trade"]) / sl_d
        qty = min(qty, (equity * config.RISK["max_notional_pct"]) / entry)
        if qty * entry < config.RISK["min_notional_usd"]:
            continue

        t = ex.SimTrade(symbol="SIM", system="DAY", side=side, entry_time=entry_ts,
                        entry=entry, qty=qty, tp=tp, sl=sl, atr=atr, hold_hours=hold_h)
        bars = [(ot.iloc[k], P["o"][k], P["h"][k], P["l"][k], P["c"][k])
                for k in range(start_i, min(n, start_i + 400))]
        ex.monitor(t, bars, fee_bps=fee_bps,
                   funding_rate=config.FUNDING_RATE_8H if config.FUNDING_ENABLED else 0.0,
                   worst_case=worst_case, start_ts=start_ts)
        # "فوري" = أُغلقت على شمعة الإشارة نفسها (مستحيل في الواقع)
        t.immediate = (start_i == i and t.bars_held <= 1)
        trades.append(t)
    return trades


# ───────────────────────── FALCON (نسخة سريعة) ─────────────────────────
def falcon_signal(P4, i, dc, de50, de200, p):
    c, o, a, dx, v, vs = P4["c"][i], P4["o"][i], P4["a"][i], \
                         P4["dx"][i], P4["v"][i], P4["vs"][i]
    dh = P4["dh"][i]
    if np.isnan([a, dx, vs, dh]).any():
        return 0, None, None, "nan"
    if not (dc > de200 and de50 > de200):
        return 0, None, None, "regime-bear"
    ap = a / c
    if not (0.002 < ap < 0.06):
        return 0, None, None, "atr-range"
    if v < p["vol_mult"] * vs:
        return 0, None, None, "vol"
    if dx < p["adx_min"]:
        return 0, None, None, "adx"
    if c > dh and (c - o) > p["mom_mult"] * a:
        return 1, float(c), float(a), None
    return 0, None, None, "no-breakout"


def run_falcon(d4, d15, dd, p, mode, equity=config.PAPER_EQUITY,
               fee_bps=config.FEE_BPS, worst_case=True):
    P4 = prep(d4, 20)
    P4["dh"] = pd.Series(P4["h"]).shift(1).rolling(p["don"]).max().to_numpy(float)
    P15 = prep(d15, 20)
    # النظام اليومي: آخر إغلاق يومي جزئي + EMA تزايدي
    dd_c = dd["close"].to_numpy(float)
    e50 = st.ema(pd.Series(dd_c), 50).to_numpy(float)
    e200 = st.ema(pd.Series(dd_c), 200).to_numpy(float)
    dts = dd["open_time"]

    hold_h = p["max_hold_bars"] * 4
    trades = []
    for i in range(220, len(d4) - 2):
        t0 = d4["open_time"].iloc[i]
        k = int(np.searchsorted(dts.values.astype("datetime64[ns]"),
                                np.datetime64(t0.tz_convert("UTC").tz_localize(None)),
                                side="right")) - 1
        if k < 210:
            continue
        side, px, atr, rej = falcon_signal(P4, i, dd_c[k], e50[k], e200[k], p)
        if side == 0 or atr is None:
            continue

        if mode == "old":
            entry = float(P4["c"][i])
            entry_ts = t0 + timedelta(hours=4)
            start_ts = None
        else:
            entry = float(P4["o"][i + 1])
            entry = ex.apply_cost(entry, side, ex.SLIPPAGE_SIM_BPS)
            entry_ts = d4["open_time"].iloc[i + 1]
            start_ts = entry_ts

        tp, sl = ex.plan_exit(side, entry, atr, p["tp_atr"], p["sl_atr"])
        sl_d = abs(entry - sl) or p["sl_atr"] * atr
        ot15 = P15["ot"]
        s0 = int(np.searchsorted(ot15.values.astype("datetime64[ns]"),
                                 np.datetime64(entry_ts.tz_convert("UTC").tz_localize(None)),
                                 side="left"))
        for leg, frac, leg_tp in (("A", 0.5, tp), ("B", 0.5, 0.0)):
            qty = (equity * p["risk_per_trade"] * frac) / sl_d
            qty = min(qty, (equity * config.RISK["max_notional_pct"]) / entry)
            if qty * entry < config.RISK["min_notional_usd"]:
                continue
            t = ex.SimTrade(symbol="SIM", system="FALCON", side=side, leg=leg,
                            entry_time=entry_ts, entry=entry, qty=qty, tp=leg_tp,
                            sl=sl, atr=atr, hold_hours=hold_h)
            bars = [(ot15.iloc[k2], P15["o"][k2], P15["h"][k2], P15["l"][k2], P15["c"][k2])
                    for k2 in range(max(0, s0 - 1), min(len(d15), s0 + 4000))]
            ex.monitor(t, bars, fee_bps=fee_bps,
                       funding_rate=config.FUNDING_RATE_8H if config.FUNDING_ENABLED else 0.0,
                       worst_case=worst_case, start_ts=start_ts,
                       trail_atr=p["trail_atr"] if leg == "B" else 0.0)
            trades.append(t)
    return trades
