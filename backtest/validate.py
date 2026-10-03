"""
يتحقق أن المحاكي السريع يطابق الكود الأصلي حرفياً.
أي اختلاف يعني أن نتائج المقارنة بلا قيمة.
"""
from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, strategy as st
from backtest.sim import fetch
from backtest.fast import prep, prep_hourly, h1_at, day_signal

TOL = 1e-9


def check(name, a, b, tol=TOL):
    ok = (a is None and b is None) or (
        a is not None and b is not None and abs(float(a) - float(b)) <= tol)
    print(f"  {'✔' if ok else '❌'} {name}:  أصلي={a}  سريع={b}")
    return ok


def main(bars=1200, symbol="SOLUSDT", step=37):
    print(f"التحقق من تطابق المحاكي السريع مع الكود الأصلي — {symbol}, {bars} شمعة")
    df = fetch(symbol, "15m", bars)
    btc = fetch(config.ANCHOR, "15m", bars)
    P = prep(df, 96)
    codes, H, e = prep_hourly(df)
    bidx = __import__("pandas").Series(
        __import__("numpy").arange(len(btc), dtype=int), index=__import__("pandas").Index(btc["open_time"]))
    apos = df["open_time"].map(bidx).fillna(-1).astype(int).to_numpy()

    ok_h1 = ok_sig = True
    checked = 0
    for i in range(150, len(df) - 2, step):
        # 1) h1_now
        sub = df.iloc[:i + 1]
        c_ref, e_ref = st.h1_now(sub)
        c_fast, e_fast = h1_at(codes, e, i, P["c"][i])
        if (c_ref is None) != (c_fast is None):
            ok_h1 = False
            print(f"  ❌ h1_now عند i={i}: أصلي={c_ref} سريع={c_fast}")
        elif c_ref is not None and abs(c_ref - c_fast) > 1e-9:
            ok_h1 = False
            print(f"  ❌ h1_now عند i={i}: {c_ref} != {c_fast}")
        # 2) الإشارة
        j = apos[i]
        if j >= 100:
            a_now = st.anchor_ar_now(btc.iloc[j - 129:j + 1])
            s_ref, px_ref, a_ref, _ = st.day_scan_debug(sub, a_now, c_ref, e_ref, config.DAY)
            s_f, px_f, a_f, _ = day_signal(P, i, a_now, c_fast, e_fast, config.DAY)
            if s_ref != s_f:
                ok_sig = False
                print(f"  ❌ الإشارة عند i={i}: أصلي={s_ref} سريع={s_f}")
            elif s_ref != 0 and abs(px_ref - px_f) > 1e-9:
                ok_sig = False
                print(f"  ❌ سعر الإشارة عند i={i}: {px_ref} != {px_f}")
            checked += 1

    print(f"\n  قورنت {checked} شمعة.")
    print(f"  h1_now : {'✔ مطابق' if ok_h1 else '❌ غير مطابق'}")
    print(f"  الإشارة: {'✔ مطابق' if ok_sig else '❌ غير مطابق'}")
    return ok_h1 and ok_sig


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
