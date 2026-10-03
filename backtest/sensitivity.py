"""
اختبار حساسية: هل الاستراتيجية تخسر بسبب نموذج التكاليف الذي أضفناه،
أم أنّ هامشها الأصلي أصلًا رقيق؟

يُشغّل المحرك المصحّح بمستويات تكلفة مختلفة. إذا انهارت النتائج عند
أدنى تكلفة فهذا يعني أن الهامش وهمي؛ وإذا صمدت فهي حساسة للتكاليف فقط.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, exec as ex
from backtest.sim import fetch
from backtest import fast


def main(bars=15000):
    btc = fetch(config.ANCHOR, "15m", bars + 300)
    data = {s: fetch(s, "15m", bars) for s in config.DAY_SYMBOLS}
    print(f"جُهّزت البيانات: {len(data)} رمز × {bars:,} شمعة\n")

    scenarios = [
        ("بلا تكاليف (إجمالي)", 0.0, 0.0),
        ("واقعي (2 + 2 bps)", 2.0, 2.0),
        ("متحفظ (5 + 2 bps)", 5.0, 2.0),
        ("سيء (10 + 4 bps)", 10.0, 4.0),
    ]
    print(f"  {'السيناريو':<26}{'ن':>6}{'نسبة الفوز %':>14}{'PF':>8}{'الصافي $':>14}{'متوسط R':>10}")
    print("  " + "─" * 78)
    for name, slip, fee in scenarios:
        ex.SLIPPAGE_SIM_BPS = slip
        trades = []
        for s_, df in data.items():
            trades += fast.run_day(df, btc, config.DAY, "new", fee_bps=fee)
        st_ = ex.stats(trades)
        print(f"  {name:<26}{st_['n']:>6}{st_['wr']:>14.1f}{st_['pf']:>8.2f}"
              f"{st_['net']:>14,.0f}{st_['avg_r']:>10.3f}")

    print("\n  القراءة: إن كان PF يقترب من 1.0 عند التكاليف الواقعية، فالاستراتيجية")
    print("  على الحافة — أي انزلاق إضافي أو تأخير تنفيذ يقلبها إلى خسارة.")


if __name__ == "__main__":
    main(bars=int(sys.argv[1]) if len(sys.argv) > 1 else 15000)
