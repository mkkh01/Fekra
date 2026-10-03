"""
التقرير النهائي: هل الإصلاحات تحسّن جودة الصفقات أم تضرّها؟

يشغّل ثلاثة مسارات على نفس الإشارات ونفس البيانات:
  1) القديم (الإنتاج الآن)      — دخول على إغلاق شمعة الإشارة + إدارة من نفس الشمعة
  2) الجديد (متحفظ)             — تنفيذ واقعي + عند لمس الوقف والهدف معاً نفترض الوقف
  3) الجديد (متفائل)            — نفس التنفيذ لكن نفترض الهدف أولاً

المساران 2 و 3 يكوّنان **نطاقاً** لأن ترتيب الأحداث داخل الشمعة غير معروف من OHLC.
الحقيقة تقع بينهما، وأي رقم خارج هذا النطاق يشير إلى خلل في المحرك.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, exec as ex
from backtest.sim import fetch
from backtest import fast


def line(label, a, b, c, fmt="{:,.2f}"):
    print(f"  {label:<30}{fmt.format(a):>16}{fmt.format(b):>18}{fmt.format(c):>18}")


def report(title, res, ref=None):
    old, new_w, new_o, moc = res
    so, sw, spo, sm = ex.stats(old), ex.stats(new_w), ex.stats(new_o), ex.stats(moc)
    print(f"\n{'═'*96}\n{title}\n{'═'*96}")
    if ref:
        print(f"  مرجع الباك تست المعلن في الكود:  n={ref['n']:,}  WR={ref['wr']}%  PF={ref['pf']}")
    print(f"  {'المقياس':<30}{'القديم (الإنتاج)':>16}{'سعر الإغلاق':>18}{'أول سعر متاح':>18}")
    print("  " + "─" * 82)
    for lab, k, f in [("عدد الصفقات", "n", "{:,.0f}"),
                      ("نسبة الفوز %", "wr", "{:.1f}"),
                      ("عامل الربح PF", "pf", "{:.2f}"),
                      ("الصافي $ (رأس مال 10k)", "net", "{:,.2f}"),
                      ("متوسط R لكل صفقة", "avg_r", "{:.3f}"),
                      ("متوسط مدة الاحتفاظ (ساعة)", "avg_hold_h", "{:.2f}")]:
        print(f"  {lab:<30}{f.format(so[k]):>16}{f.format(sm[k]):>18}{f.format(sw[k]):>18}")
    io_, iw = so["immediate"], sw["immediate"]
    print("  " + "─" * 82)
    print(f"  {'صفقات أُغلقت على شمعة الإشارة':<30}{io_:>16}{sm['immediate']:>18}{iw:>18}")
    print(f"  {'← كنسبة مئوية':<30}"
          f"{(io_/so['n']*100 if so['n'] else 0):>15.1f}%"
          f"{(sm['immediate']/sm['n']*100 if sm['n'] else 0):>17.1f}%"
          f"{(iw/sw['n']*100 if sw['n'] else 0):>17.1f}%")
    print(f"\n  أسباب الخروج — القديم      : {so.get('reasons')}")
    print(f"  أسباب الخروج — سعر الإغلاق : {sm.get('reasons')}")
    print(f"  أسباب الخروج — أول سعر     : {sw.get('reasons')}")

    # فحص السلامة: مدة احتفاظ سالبة = دليل قاطع على السفر في الزمن
    neg = sum(1 for t in old
              if t.exit_time and t.entry_time and t.exit_time < t.entry_time)
    print(f"\n  ⚠️  صفقات أُغلقت **قبل** لحظة دخولها (مستحيل فيزيائياً): "
          f"{neg} من {so['n']} في المحرك القديم")
    return so, sw, spo


def main(bars=15000, do_falcon=True):
    print(f"جلب {bars:,} شمعة لكل رمز ... (قد يستغرق دقائق)")
    btc = fetch(config.ANCHOR, "15m", bars + 300)

    old, new_w, new_o, moc = [], [], [], []
    for s_ in config.DAY_SYMBOLS:
        df = fetch(s_, "15m", bars)
        o = fast.run_day(df, btc, config.DAY, "old")
        nw = fast.run_day(df, btc, config.DAY, "new", worst_case=True)
        mc = fast.run_day(df, btc, config.DAY, "moc", worst_case=True)
        old += o
        new_w += nw
        moc += mc
        print(f"  ▸ {s_:10} قديم n={len(o):4} WR={ex.stats(o)['wr']:5.1f}%  |  "
              f"إغلاق n={len(mc):4} WR={ex.stats(mc)['wr']:5.1f}% PF={ex.stats(mc)['pf']:.2f}  |  "
              f"فتح n={len(nw):4} WR={ex.stats(nw)['wr']:5.1f}% PF={ex.stats(nw)['pf']:.2f}")
    report("نظام FALCON-DAY (15m) — أثر الإصلاحات على جودة الصفقات",
           (old, new_w, new_o, moc), config.BACKTEST_REF["DAY"])

    if do_falcon:
        print("\n\nجلب بيانات FALCON ...")
        fo, fw, fpo = [], [], []
        for s_ in config.FALCON_SYMBOLS:
            d4 = fetch(s_, "4h", 1500)
            dd = fetch(s_, "1d", 600)
            d15 = fetch(s_, "15m", max(bars, 24000))   # يغطّي نفس مدى شموع 4h
            a1 = fast.run_falcon(d4, d15, dd, config.FALCON, "old")
            a2 = fast.run_falcon(d4, d15, dd, config.FALCON, "new", worst_case=True)
            a3 = fast.run_falcon(d4, d15, dd, config.FALCON, "moc", worst_case=True)
            fo += a1
            fw += a2
            fpo += a3
            print(f"  ▸ {s_:10} قديم n={len(a1):4}  فتح n={len(a2):4}  إغلاق n={len(a3):4}")
        report("نظام FALCON (4h) — أثر الإصلاحات على جودة الصفقات",
               (fo, fw, fpo, fpo), config.BACKTEST_REF["FALCON"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=15000)
    ap.add_argument("--no-falcon", action="store_true")
    a = ap.parse_args()
    main(bars=a.bars, do_falcon=not a.no_falcon)
