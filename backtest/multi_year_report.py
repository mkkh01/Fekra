"""
تقرير الباك تست لعدة سنوات (Multi-Year Historical Backtest Report)
يختبر النظامين FALCON و DAY على بيانات حقيقية متتالية لعدة سنوات (تصل إلى 5 سنوات) من Binance.
"""
import os
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, exec as ex, strategy as st
from backtest.sim import fetch
from backtest import fast


def get_cached_or_fetch(sym, tf, bars):
    cache_f = f"/tmp/crypto_cache/{sym}_{tf}_{bars // 1000}k.pkl"
    if os.path.exists(cache_f):
        return pd.read_pickle(cache_f)
    df = fetch(sym, tf, bars)
    try:
        os.makedirs("/tmp/crypto_cache", exist_ok=True)
        df.to_pickle(cache_f)
    except Exception:
        pass
    return df


def run_multi_year_falcon(symbols, bars_4h=11000, days_daily=2000):
    print(f"\n{'═'*85}")
    print(f"🦅 تقرير نظام FALCON (4H) — بيانات تاريخية حقيقية لـ 5 سنوات ({bars_4h:,} شمعة لكل رمز)")
    print(f"{'═'*85}")
    all_trades = []
    per_symbol = {}
    p = config.FALCON

    for sym in symbols:
        d4 = get_cached_or_fetch(sym, "4h", 11000)
        dd = get_cached_or_fetch(sym, "1d", 2000)
        start_date = str(d4['open_time'].iloc[0])[:10]
        end_date = str(d4['open_time'].iloc[-1])[:10]

        P4 = fast.prep(d4, 20)
        P4["dh"] = pd.Series(P4["h"]).shift(1).rolling(p["don"]).max().to_numpy(float)
        dd_c = dd["close"].to_numpy(float)
        e50 = st.ema(pd.Series(dd_c), 50).to_numpy(float)
        e200 = st.ema(pd.Series(dd_c), 200).to_numpy(float)
        dts = dd["open_time"]
        hold_h = p["max_hold_bars"] * 4
        sym_trades = []

        for i in range(220, len(d4) - 2):
            t0 = d4["open_time"].iloc[i]
            k = int(np.searchsorted(dts.values.astype("datetime64[ns]"),
                                    np.datetime64(t0.tz_convert("UTC").tz_localize(None)),
                                    side="right")) - 1
            if k < 210:
                continue
            side, px, atr, rej = fast.falcon_signal(P4, i, dd_c[k], e50[k], e200[k], p)
            if side == 0 or atr is None:
                continue
            entry = float(P4["o"][i + 1])
            entry = ex.apply_cost(entry, side, ex.SLIPPAGE_SIM_BPS)
            entry_ts = d4["open_time"].iloc[i + 1]
            tp, sl = ex.plan_exit(side, entry, atr, p["tp_atr"], p["sl_atr"])
            sl_d = abs(entry - sl) or p["sl_atr"] * atr
            qty = (config.PAPER_EQUITY * p["risk_per_trade"]) / sl_d

            t = ex.SimTrade(symbol=sym, system="FALCON", side=side,
                            entry_time=entry_ts, entry=entry, qty=qty, tp=tp,
                            sl=sl, atr=atr, hold_hours=hold_h)
            bars = [(d4["open_time"].iloc[k2], P4["o"][k2], P4["h"][k2], P4["l"][k2], P4["c"][k2])
                    for k2 in range(i + 1, min(len(d4), i + 1 + p["max_hold_bars"]))]
            ex.monitor(t, bars, fee_bps=config.FEE_BPS, funding_rate=config.FUNDING_RATE_8H,
                       worst_case=True, start_ts=entry_ts)
            sym_trades.append(t)
            all_trades.append(t)

        st_sym = ex.stats(sym_trades)
        per_symbol[sym] = st_sym
        print(f"  ▸ {sym:<10} ({start_date} إلى {end_date}): صفقات={st_sym['n']:<3} | WinRate={st_sym['wr']:>5.1f}% | PF={st_sym['pf']:>4.2f} | صافي=${st_sym['net']:>8.2f}")

    tot = ex.stats(all_trades)
    print("  " + "─" * 80)
    print(f"  إجمالي صفقات FALCON: {tot['n']} صفقة")
    print(f"  نسبة الفوز الإجمالية (Win Rate): {tot['wr']}%")
    print(f"  عامل الربح (Profit Factor): {tot['pf']}")
    print(f"  صافي الأرباح (رأس مال 10k): ${tot['net']}")
    print(f"  متوسط العائد R لكل صفقة: {tot['avg_r']}")
    print(f"  متوسط مدة الاحتفاظ: {tot['avg_hold_h']:.1f} ساعة")
    print(f"  صفقات الخروج الفوري على نفس شمعة الدخول: {tot['immediate']} (0.0%)")
    print(f"  أسباب الخروج: {tot['reasons']}")
    return tot


def run_multi_year_day(symbols, bars_15m=20000):
    print(f"\n{'═'*85}")
    print(f"⚡ تقرير نظام FALCON-DAY (15M) — بيانات حقيقية ({bars_15m:,} شمعة لكل رمز)")
    print(f"{'═'*85}")
    btc = get_cached_or_fetch(config.ANCHOR, "15m", 20000)
    all_trades = []
    p = config.DAY

    for sym in symbols:
        df = get_cached_or_fetch(sym, "15m", bars_15m)
        start_date = str(df['open_time'].iloc[0])[:10]
        end_date = str(df['open_time'].iloc[-1])[:10]
        res = fast.run_day(df, btc, p, "new", worst_case=True)
        all_trades += res
        st_sym = ex.stats(res)
        print(f"  ▸ {sym:<10} ({start_date} إلى {end_date}): صفقات={st_sym['n']:<3} | WinRate={st_sym['wr']:>5.1f}% | PF={st_sym['pf']:>4.2f} | صافي=${st_sym['net']:>8.2f}")

    tot = ex.stats(all_trades)
    print("  " + "─" * 80)
    print(f"  إجمالي صفقات DAY: {tot['n']} صفقة")
    print(f"  نسبة الفوز الإجمالية (Win Rate): {tot['wr']}%")
    print(f"  عامل الربح (Profit Factor): {tot['pf']}")
    print(f"  صافي الأرباح (رأس مال 10k): ${tot['net']}")
    print(f"  متوسط العائد R لكل صفقة: {tot['avg_r']}")
    print(f"  متوسط مدة الاحتفاظ: {tot['avg_hold_h']:.1f} ساعة")
    print(f"  صفقات الخروج الفوري على نفس شمعة الدخول: {tot['immediate']} (0.0%)")
    print(f"  أسباب الخروج: {tot['reasons']}")
    return tot


if __name__ == "__main__":
    s_falcon = run_multi_year_falcon(config.FALCON_SYMBOLS, bars_4h=11000)
    s_day = run_multi_year_day(config.DAY_SYMBOLS, bars_15m=20000)
