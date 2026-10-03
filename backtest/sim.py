"""
محاكي مقارن: هل الإصلاحات تضر بجودة الصفقات أم تصححها؟

يشغّل **نفس** منطق الإشارة (app/strategy.py) مرتين:
  A) المحرك القديم (الحالي في الإنتاج): دخول على إغلاق شمعة الإشارة + إدارة تبدأ من نفس الشمعة
  B) المحرك الجديد (app/exec.py):       دخول على السعر القابل للتنفيذ + إدارة تبدأ بعد لحظة الدخول فقط

ثم يقارن: نسبة الفوز، PF، الصافي، مدة الاحتفاظ، ونسبة "الفتح والإغلاق الفوري".
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, strategy as st, exec as ex

BASE = "https://data-api.binance.vision"
S = requests.Session()
S.headers.update({"User-Agent": "CT-FALCON/backtest"})


# ────────────────────────────── البيانات ──────────────────────────────
def fetch(symbol, interval, total):
    """يجلب شموعاً تاريخية مع ترقيم الصفحات (Binance يحدّ بـ 1000 لكل طلب)."""
    out, end = [], None
    while len(out) < total:
        n = min(1000, total - len(out))
        p = {"symbol": symbol, "interval": interval, "limit": n}
        if end:
            p["endTime"] = end
        r = S.get(f"{BASE}/api/v3/klines", params=p, timeout=25)
        rows = r.json()
        if not rows:
            break
        out = rows + out
        end = int(rows[0][0]) - 1
    df = pd.DataFrame(out, columns=["open_time", "open", "high", "low", "close",
                                    "volume", "ct", "qv", "tr", "tb", "tq", "ig"])
    df = df[["open_time", "open", "high", "low", "close", "volume"]].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
    return df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)


def bar_tuple(df, i):
    r = df.iloc[i]
    return (r["open_time"], r["open"], r["high"], r["low"], r["close"])


# ────────────────────────────── المحرك ──────────────────────────────
def anchor_positions(sym_df, btc_df):
    """يحاذي شموع المرساة (BTC) مع شموع الرمز — الفلتر يقرأ حالة BTC لا حالة الرمز."""
    # ⚠️ لا تستخدم .values هنا: يحوّل التواريخ إلى datetime64 غير واعٍ بالمنطقة الزمنية
    # فتفشل المطابقة مع شموع الرمز الواعية بالمنطقة.
    idx = pd.Series(np.arange(len(btc_df), dtype=int), index=pd.Index(btc_df["open_time"]))
    return sym_df["open_time"].map(idx).fillna(-1).astype(int)


def run_day(df, p, mode, btc_df=None, equity=config.PAPER_EQUITY, fee_bps=config.FEE_BPS,
            funding_rate=config.FUNDING_RATE_8H):
    """
    mode = 'old'  → دخول على إغلاق شمعة الإشارة، إدارة تبدأ من الشمعة نفسها (الخلل الحالي)
    mode = 'new'  → دخول على افتتاح الشمعة التالية + سبريد/انزلاق، إدارة تبدأ بعد الدخول
    """
    trades, n = [], len(df)
    hold_h = p["max_hold_bars"] * 0.25
    apos = anchor_positions(df, btc_df) if btc_df is not None else None
    for i in range(120, n - 2):
        sub = df.iloc[:i + 1]
        h1c, h1e = st.h1_now(sub)
        if h1c is None:
            continue
        if btc_df is not None:
            j = apos[i]
            if j < 100:
                continue
            a_now = st.anchor_ar_now(btc_df.iloc[max(0, j - 129):j + 1])
        else:
            a_now = 0.0
        side, px, atr, dbg = st.day_scan_debug(sub, a_now, h1c, h1e, p)
        if side == 0 or atr is None:
            continue

        if mode == "old":
            # ❌ كما هو الآن في الإنتاج: سعر الدخول = إغلاق شمعة الإشارة
            entry = float(df["close"].iloc[i])
            entry_ts = df["open_time"].iloc[i] + timedelta(minutes=15)
            start_i = i                       # ❌ الإدارة تبدأ من شمعة الدخول نفسها
            start_ts = None                   # ❌ لا يوجد شرط زمني
        else:
            # ✅ التنفيذ على أول سعر متاح بعد اكتشاف الإشارة = افتتاح الشمعة التالية
            entry = float(df["open"].iloc[i + 1])
            # سبريد + انزلاق في اتجاه يضرّك
            entry = ex.apply_cost(entry, side, ex.SLIPPAGE_SIM_BPS)
            entry_ts = df["open_time"].iloc[i + 1]
            start_i = i + 1
            start_ts = entry_ts               # ✅ لا نقيّم شيئاً قبل لحظة الدخول

        tp, sl = ex.plan_exit(side, entry, atr, p["tp_atr"], p["sl_atr"])
        sl_d = abs(entry - sl) or p["sl_atr"] * atr
        qty = (equity * p["risk_per_trade"]) / sl_d
        qty = min(qty, (equity * config.RISK["max_notional_pct"]) / entry)
        if qty * entry < config.RISK["min_notional_usd"]:
            continue

        t = ex.SimTrade(symbol="SIM", system="DAY", side=side, entry_time=entry_ts,
                        entry=entry, qty=qty, tp=tp, sl=sl, atr=atr, hold_hours=hold_h)
        bars = [bar_tuple(df, k) for k in range(start_i, min(n, start_i + 400))]
        ex.monitor(t, bars, fee_bps=fee_bps,
                   funding_rate=funding_rate if config.FUNDING_ENABLED else 0.0,
                   worst_case=True, start_ts=start_ts)
        t.immediate = (t.bars_held <= 1 and t.reason in ("TP", "SL"))
        trades.append(t)
    return trades


def run_falcon(d4, d15, dd, p, mode, equity=config.PAPER_EQUITY,
               fee_bps=config.FEE_BPS, funding_rate=config.FUNDING_RATE_8H):
    trades = []
    hold_h = p["max_hold_bars"] * 4
    for i in range(220, len(d4) - 2):
        sub = d4.iloc[:i + 1]
        t0 = sub["open_time"].iloc[-1]
        hist = dd[dd["open_time"] <= t0].tail(250)
        if len(hist) < 210:
            continue
        dc, de50, de200 = st.daily_regime(hist)
        side, px, atr, dbg = st.falcon_scan_debug(sub, dc, de50, de200, p)
        if side == 0 or atr is None:
            continue

        if mode == "old":
            entry = float(d4["close"].iloc[i])          # ❌ سعر قديم حتى 4 ساعات
            entry_ts = t0 + timedelta(hours=4)
            start_ts = None                             # ❌ بلا شرط زمني
        else:
            entry = float(d4["open"].iloc[i + 1])       # ✅ أول سعر متاح بعد الإشارة
            entry = ex.apply_cost(entry, side, ex.SLIPPAGE_SIM_BPS)
            entry_ts = d4["open_time"].iloc[i + 1]
            start_ts = entry_ts

        tp, sl = ex.plan_exit(side, entry, atr, p["tp_atr"], p["sl_atr"])
        sl_d = abs(entry - sl) or p["sl_atr"] * atr
        for leg, frac, leg_tp in (("A", 0.5, tp), ("B", 0.5, 0.0)):
            qty = (equity * p["risk_per_trade"] * frac) / sl_d
            qty = min(qty, (equity * config.RISK["max_notional_pct"]) / entry)
            if qty * entry < config.RISK["min_notional_usd"]:
                continue
            t = ex.SimTrade(symbol="SIM", system="FALCON", side=side, leg=leg,
                            entry_time=entry_ts, entry=entry, qty=qty,
                            tp=leg_tp, sl=sl, atr=atr, hold_hours=hold_h)
            win = d15[(d15["open_time"] >= (entry_ts - timedelta(minutes=15)))]
            bars = [(r["open_time"], r["open"], r["high"], r["low"], r["close"])
                    for _, r in win.head(4000).iterrows()]
            ex.monitor(t, bars, fee_bps=fee_bps,
                       funding_rate=funding_rate if config.FUNDING_ENABLED else 0.0,
                       worst_case=True, start_ts=start_ts,
                       trail_atr=p["trail_atr"] if leg == "B" else 0.0)
            t.immediate = (t.bars_held <= 1 and t.reason in ("TP", "SL"))
            trades.append(t)
    return trades


# ────────────────────────────── التقرير ──────────────────────────────
def compare(name, old, new, ref=None):
    so, sn = ex.stats(old), ex.stats(new)
    print(f"\n{'═'*88}\n{name}\n{'═'*88}")
    if ref:
        print(f"  مرجع الباك تست المعلن: n={ref['n']}  WR={ref['wr']}%  PF={ref['pf']}")
    print(f"  {'المقياس':<26}{'القديم (الإنتاج)':>22}{'الجديد (بعد الإصلاح)':>26}")
    print(f"  {'-'*74}")
    for label, key, fmt in [
        ("عدد الصفقات", "n", "{:,.0f}"),
        ("نسبة الفوز %", "wr", "{:.1f}"),
        ("عامل الربح PF", "pf", "{:.2f}"),
        ("الصافي $", "net", "{:,.2f}"),
        ("متوسط R", "avg_r", "{:.3f}"),
        ("متوسط مدة الاحتفاظ (س)", "avg_hold_h", "{:.2f}"),
        ("صفقات أُغلقت فوراً", "immediate", "{:,.0f}"),
    ]:
        print(f"  {label:<26}{fmt.format(so[key]):>22}{fmt.format(sn[key]):>26}")
    po = (so["immediate"] / so["n"] * 100) if so["n"] else 0
    pn = (sn["immediate"] / sn["n"] * 100) if sn["n"] else 0
    print(f"  {'نسبة الإغلاق الفوري %':<26}{po:>22.1f}{pn:>26.1f}")
    print(f"\n  أسباب الخروج — القديم: {so.get('reasons')}")
    print(f"  أسباب الخروج — الجديد: {sn.get('reasons')}")
    return so, sn


def main(symbols=None, bars=4000, do_falcon=True):
    symbols = symbols or config.DAY_SYMBOLS
    print(f"جلب {bars:,} شمعة 15m لكل رمز من {len(symbols)} رموز + مرساة BTC...")
    ref = config.BACKTEST_REF["DAY"]
    btc = fetch(config.ANCHOR, "15m", bars + 200)

    old_all, new_all = [], []
    for s_ in symbols:
        df = fetch(s_, "15m", bars)
        o = run_day(df, config.DAY, "old", btc_df=btc)
        n = run_day(df, config.DAY, "new", btc_df=btc)
        old_all += o
        new_all += n
        so, sn = ex.stats(o), ex.stats(n)
        print(f"  {s_:10} قديم: n={so['n']:3} WR={so['wr']:5.1f}% فوري={so['immediate']:3}  |  "
              f"جديد: n={sn['n']:3} WR={sn['wr']:5.1f}% فوري={sn['immediate']:3}")
    compare("نظام FALCON-DAY  (15m) — قبل الإصلاح مقابل بعده", old_all, new_all, ref)

    if do_falcon:
        print("\n\nجلب بيانات FALCON (4h + 1d + 15m)...")
        fo, fn = [], []
        for s_ in config.FALCON_SYMBOLS:
            d4 = fetch(s_, "4h", 700)
            dd = fetch(s_, "1d", 400)
            d15 = fetch(s_, "15m", 9000)
            fo += run_falcon(d4, d15, dd, config.FALCON, "old")
            fn += run_falcon(d4, d15, dd, config.FALCON, "new")
        compare("نظام FALCON  (4h) — قبل الإصلاح مقابل بعده", fo, fn,
                config.BACKTEST_REF["FALCON"])


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=4000)
    ap.add_argument("--symbols", type=str, default="")
    ap.add_argument("--no-falcon", action="store_true")
    a = ap.parse_args()
    main(symbols=(a.symbols.split(",") if a.symbols else None),
         bars=a.bars, do_falcon=not a.no_falcon)
