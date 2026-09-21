"""إشارات FALCON-DAY و FALCON — نفس رياضيات الباك تست حرفياً + نسخ تشخيصية للـ summary."""
import numpy as np
import pandas as pd


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    g = d.where(d > 0, 0.0)
    l = (-d).where(d < 0, 0.0)
    ag = g.ewm(alpha=1 / n, adjust=False).mean()
    al = l.ewm(alpha=1 / n, adjust=False).mean()
    out = 100 - 100 / (1 + ag / al.replace(0, np.nan))
    return out.fillna(50)


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df, n=14):
    up = df["high"].diff()
    dn = -df["low"].diff()
    pm = np.where((up > dn) & (up > 0), up, 0.0)
    mm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(pm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a.replace(0, np.nan)
    mdi = 100 * pd.Series(mm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean().fillna(0)


def anchor_ar_now(df15_anchor):
    i = len(df15_anchor) - 1
    a = atr(df15_anchor).iloc[i]
    c = df15_anchor["close"].iloc[i]
    o = df15_anchor["open"].iloc[i]
    if np.isnan(a) or a == 0:
        return 999
    return float((c - o) / a)


def h1_now(df15):
    h1 = df15.set_index("open_time")["close"].resample("1h").last().dropna()
    if len(h1) < 60:
        return None, None
    e = ema(h1, 50)
    return float(h1.iloc[-1]), float(e.iloc[-1])


def daily_regime(df_daily):
    if len(df_daily) < 210:
        return None, None, None
    e50 = ema(df_daily["close"], 50).iloc[-1]
    e200 = ema(df_daily["close"], 200).iloc[-1]
    return float(df_daily["close"].iloc[-1]), float(e50), float(e200)


# ═══════════════ FALCON-DAY (تشخيصي: يعيد سبب الرفض أيضاً) ═══════════════
def day_scan_debug(df15, anchor_ar, h1c, h1e, p):
    dbg = {"reject": None}
    i = len(df15) - 1
    if i < 100:
        dbg["reject"] = "warmup"
        return 0, None, None, dbg
    if h1c is None or h1e is None or (isinstance(h1c, float) and np.isnan(h1c)):
        dbg["reject"] = "h1-missing"
        return 0, None, None, dbg
    c = df15["close"].to_numpy()
    o = df15["open"].to_numpy()
    a = atr(df15).to_numpy()
    r7 = rsi(df15["close"], 7).to_numpy()
    dx = adx(df15).to_numpy()
    v = df15["volume"].to_numpy()
    vs = pd.Series(v).rolling(96).mean().to_numpy()
    if np.isnan([a[i], r7[i], dx[i], vs[i]]).any():
        dbg["reject"] = "nan"
        return 0, None, None, dbg
    if not (v[i] > p["vol_mult"] * vs[i]):
        dbg["reject"] = "vol"
        return 0, None, None, dbg
    ap = a[i] / c[i]
    if not (p["atr_min_pct"] < ap < p["atr_max_pct"]):
        dbg["reject"] = "atr-range"
        return 0, None, None, dbg
    if dx[i] > p["adx_max"]:
        dbg["reject"] = "adx"
        return 0, None, None, dbg
    ar = (c[i] - o[i]) / a[i]
    anchor_ok = abs(anchor_ar) < p["btc_cap"]
    dbg["trig"] = dict(dev_L=bool(ar < -p["dev"]), dev_S=bool(ar > p["dev"]),
                       anchor=bool(anchor_ok), rsi_L=bool(r7[i] < p["rsiX"]),
                       rsi_S=bool(r7[i] > 100 - p["rsiX"]),
                       h1_L=bool(h1c > h1e), h1_S=bool(h1c < h1e))
    if ar < -p["dev"] and anchor_ok and r7[i] < p["rsiX"] and h1c > h1e:
        return 1, float(c[i]), float(a[i]), dbg
    if ar > p["dev"] and anchor_ok and r7[i] > 100 - p["rsiX"] and h1c < h1e:
        return -1, float(c[i]), float(a[i]), dbg
    dbg["reject"] = "no-trigger"
    return 0, None, None, dbg


# ═══════════════ FALCON 4H (تشخيصي) ═══════════════
def falcon_scan_debug(df4h, d_close, d_e50, d_e200, p):
    dbg = {"reject": None}
    i = len(df4h) - 1
    if i < 220 or d_e200 is None or (isinstance(d_e200, float) and np.isnan(d_e200)):
        dbg["reject"] = "warmup"
        return 0, None, None, dbg
    c = df4h["close"].to_numpy()
    o = df4h["open"].to_numpy()
    h = df4h["high"].to_numpy()
    a = atr(df4h).to_numpy()
    dx = adx(df4h).to_numpy()
    v = df4h["volume"].to_numpy()
    vs = pd.Series(v).rolling(20).mean().to_numpy()
    dh = pd.Series(h).shift(1).rolling(p["don"]).max().to_numpy()
    if np.isnan([a[i], dx[i], vs[i], dh[i]]).any():
        dbg["reject"] = "nan"
        return 0, None, None, dbg
    bull = (d_close > d_e200) and (d_e50 > d_e200)
    if not bull:
        dbg["reject"] = "regime-bear"
        return 0, None, None, dbg
    ap = a[i] / c[i]
    if not (0.002 < ap < 0.06):
        dbg["reject"] = "atr-range"
        return 0, None, None, dbg
    if v[i] < p["vol_mult"] * vs[i]:
        dbg["reject"] = "vol"
        return 0, None, None, dbg
    if dx[i] < p["adx_min"]:
        dbg["reject"] = "adx"
        return 0, None, None, dbg
    dbg["trig"] = dict(breakout=bool(c[i] > dh[i]),
                       momentum=bool((c[i] - o[i]) > p["mom_mult"] * a[i]))
    if c[i] > dh[i] and (c[i] - o[i]) > p["mom_mult"] * a[i]:
        return 1, float(c[i]), float(a[i]), dbg
    dbg["reject"] = "no-breakout"
    return 0, None, None, dbg
