"""
اختبارات محرك التنفيذ — تحرس الإصلاحات من أن تتراجع.

أهم ما تحرسه: لا يجوز أبداً أن تُغلق صفقة ببيانات سابقة لحظة دخولها.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, exec as ex


def mk(side=1, entry=100.0, atr=1.0, tp_atr=1.0, sl_atr=2.0, leg="", hold=6):
    tp, sl = ex.plan_exit(side, entry, atr, tp_atr, sl_atr)
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    return ex.SimTrade(symbol="TEST", system="DAY", side=side, entry_time=t0,
                       entry=entry, qty=100.0, tp=tp, sl=sl, atr=atr,
                       hold_hours=hold, leg=leg)


def bars(start, n, o=100.0, h=100.0, l=100.0, c=100.0, step_min=15):
    return [(start + timedelta(minutes=step_min * k), o, h, l, c) for k in range(n)]


# ───────────────────────── الاختبارات ─────────────────────────
def test_trade_never_closes_before_entry():
    """الشرط الحاسم: لا يُقيَّم أي شيء قبل entry_time."""
    t = mk(side=1, entry=100.0, atr=1.0)
    # شمعة هابطة بشدة قبل الدخول — لولا الحاجز الزمني لأُغلقت الصفقة فوراً
    past = bars(t.entry_time - timedelta(hours=2), 5, o=110, h=110, l=80, c=80)
    ex.monitor(t, past, fee_bps=2, start_ts=t.entry_time)
    assert t.reason == "", "يجب ألا تُغلق الصفقة ببيانات سابقة للدخول"


def test_tp_hits_after_entry():
    t = mk(side=1, entry=100.0, atr=1.0)          # TP = 101
    b = bars(t.entry_time + timedelta(minutes=15), 3, o=100, h=102, l=99, c=101)
    ex.monitor(t, b, fee_bps=2, start_ts=t.entry_time)
    assert t.reason == "TP" and t.exit_price == pytest.approx(101.0)


def test_sl_hits_after_entry():
    t = mk(side=1, entry=100.0, atr=1.0)          # SL = 98
    b = bars(t.entry_time + timedelta(minutes=15), 3, o=100, h=100, l=97, c=97.5)
    ex.monitor(t, b, fee_bps=2, start_ts=t.entry_time)
    assert t.reason == "SL"


def test_worst_case_prefers_sl_when_both_touched():
    """عند لمس الوقف والهدف في الشمعة نفسها → نفترض الأسوأ (الوقف)."""
    t = mk(side=1, entry=100.0, atr=1.0)          # TP = 101, SL = 98
    b = bars(t.entry_time + timedelta(minutes=15), 1, o=100, h=105, l=95, c=100)
    ex.monitor(t, b, fee_bps=2, start_ts=t.entry_time, worst_case=True)
    assert t.reason == "SL"
    t2 = mk(side=1, entry=100.0, atr=1.0)
    ex.monitor(t2, b, fee_bps=2, start_ts=t2.entry_time, worst_case=False)
    assert t2.reason == "TP"


def test_gap_fills_at_open_not_at_stop():
    """الفجوة: الوقف market — يُنفَّذ عند السعر المتاح لا عند سعر الوقف."""
    t = mk(side=1, entry=100.0, atr=1.0)          # SL = 98
    # افتتاح عند 95 = فجوة beyond الوقف
    b = bars(t.entry_time + timedelta(minutes=15), 1, o=95, h=96, l=94, c=95)
    ex.monitor(t, b, fee_bps=2, start_ts=t.entry_time)
    assert t.reason == "SL"
    assert t.exit_price == pytest.approx(95.0), "الفجوة تُنفَّذ عند الافتتاح"


def test_time_exit():
    t = mk(side=1, entry=100.0, atr=1.0, hold=1)
    b = bars(t.entry_time + timedelta(minutes=15), 8, o=100, h=100, l=100, c=100)
    ex.monitor(t, b, fee_bps=2, start_ts=t.entry_time)
    assert t.reason == "TIME"


def test_costs_reduce_net():
    t = mk(side=1, entry=100.0, atr=1.0)
    b = bars(t.entry_time + timedelta(minutes=15), 3, o=100, h=101, l=99, c=101)
    ex.monitor(t, b, fee_bps=0, start_ts=t.entry_time)
    gross = t.net
    t2 = mk(side=1, entry=100.0, atr=1.0)
    ex.monitor(t2, b, fee_bps=5, start_ts=t2.entry_time)
    assert t2.net < gross


def test_funding_grows_with_holding_period():
    n1 = ex.funding_cost(10_000, hours_held=8, rate_per_8h=0.0001)
    n2 = ex.funding_cost(10_000, hours_held=240, rate_per_8h=0.0001)  # 10 أيام
    assert n2 == pytest.approx(n1 * 30)
    assert n2 > 0


def test_spread_and_slippage_are_positive_for_buy():
    assert ex.exec_price(1, bid=99.9, ask=100.1) == 100.1      # الشراء على ask
    assert ex.exec_price(-1, bid=99.9, ask=100.1) == 99.9      # البيع على bid
    assert ex.spread_bps(100.0, 100.1) > 0
    # صفقة كبيرة مقابل سيولة ضحلة تدفع انزلاقاً أكبر (داخل حدود السقف)
    small = ex.slippage_bps(1_000, 1_000_000)
    big = ex.slippage_bps(200_000, 1_000_000)
    assert big > small
    assert ex.slippage_bps(0, 1_000_000) <= config.SLIPPAGE["max_bps"]


def test_trailing_stop_never_retreats():
    t = mk(side=1, entry=100.0, atr=1.0, leg="B", hold=100)
    before = t.sl
    b = bars(t.entry_time + timedelta(minutes=15), 10, o=100, h=110, l=99, c=109)
    ex.monitor(t, b, fee_bps=2, start_ts=t.entry_time, trail_atr=2.0)
    assert t.sl >= before, "الوقف المتحرك يرتفع ولا يتراجع أبداً"


def test_no_trade_closes_before_its_entry_time():
    """لكل صفقة: لحظة الخروج يجب أن تكون بعد لحظة الدخول — دائماً."""
    ts = []
    for side in (1, -1):
        for hi, lo in ((105, 95), (101, 99), (100, 100)):
            t = mk(side=side, entry=100.0, atr=1.0)
            b = bars(t.entry_time - timedelta(hours=1), 4, o=100, h=hi, l=lo, c=100)
            b += bars(t.entry_time + timedelta(minutes=15), 4, o=100, h=hi, l=lo, c=100)
            ex.monitor(t, b, fee_bps=2, start_ts=t.entry_time)
            if t.reason:
                ts.append(t)
    for t in ts:
        assert t.exit_time > t.entry_time, f"{t.reason} أُغلقت قبل دخولها!"


def test_day_time_travel_regression():
    """
    استنساخ للخلل الأصلي: شمعة هابطة 1.2 ATR وهدف على بُعد 1.0 ATR فقط
    (الهدف داخل جسم الشمعة). المحرك القديم كان يُغلقها فوراً عند الهدف.
    """
    atr = 1.0
    entry = 100.0
    side = 1
    tp, sl = ex.plan_exit(side, entry, atr, 1.0, 2.0)      # TP=101, SL=98
    t = ex.SimTrade(symbol="TEST", system="DAY", side=side,
                    entry_time=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
                    entry=entry, qty=100.0, tp=tp, sl=sl, atr=atr, hold_hours=6)
    # شمعة الإشارة: تمتد من (الدخول - 15د) حتى لحظة الدخول،
    # ومداها (99.7 – 101.6) يحتوي الهدف 101 بالكامل قبل أن توجد الصفقة.
    signal_bar = [(t.entry_time - timedelta(minutes=15), 101.5, 101.6, 99.7, 100.0)]
    # ❌ المحرك القديم: يبدأ من شمعة الإشارة → يُغلق فوراً عند الهدف
    old = ex.SimTrade(**{**t.__dict__})
    ex.monitor(old, signal_bar, fee_bps=2, start_ts=None)
    assert old.reason == "TP", "المحرك القديم يُغلق فوراً (هذا هو الخلل)"
    # ✅ المحرك المصحّح: يتجاهلها تماماً
    new = ex.SimTrade(**{**t.__dict__})
    ex.monitor(new, signal_bar, fee_bps=2, start_ts=t.entry_time)
    assert new.reason == "", "المحرك المصحّح يجب ألا يرى شمعة الإشارة"


def test_zero_target_never_triggers():
    """
    الساق B في FALCON بلا هدف (tp = 0). بدون حارس tp > 0 يصبح
    (high >= 0) صحيحاً دائماً فتُغلق الصفقة بسعر صفر — خسارة كارثية.
    """
    t = mk(side=1, entry=100.0, atr=1.0)
    t.tp = 0.0
    b = bars(t.entry_time + timedelta(minutes=15), 3, o=100, h=101, l=99, c=100)
    ex.monitor(t, b, fee_bps=2, start_ts=t.entry_time)
    # السعر داخل نطاق لا يمس الوقف (98) ولا هدفاً (لا يوجد) → تبقى الصفقة مفتوحة
    assert t.reason == "", f"أُغلقت بلا سبب مشروع: {t.reason} @ {t.exit_price}"
    # ولو فرضنا شمعة ترتفع كثيراً: لا يجوز أن تُغلق عند "هدف" قيمته صفر
    t2 = mk(side=1, entry=100.0, atr=1.0)
    t2.tp = 0.0
    ex.monitor(t2, bars(t2.entry_time + timedelta(minutes=15), 3,
                        o=100, h=150, l=99, c=140),
               fee_bps=2, start_ts=t2.entry_time)
    # تبقى مفتوحة (لا هدف لها) مع تسجيل الربح العائم — لا تُغلق عند الصفر
    assert t2.reason == "", f"أُغلقت خطأً: {t2.reason} @ {t2.exit_price}"
    assert t2.mfe > 0, "يجب أن يتتبع المحرك الربح العائم للساق B"


def test_daily_limit_key_matches_counts_map():
    """
    انحدار: day_counts_map يعيد مفاتيح (system, symbol) بينما كان can_open
    يبحث بـ (day, system, symbol) → الحد اليومي لا يُطبَّق أبداً.
    """
    from app import trader
    counts = {("DAY", "SOLUSDT"): 3}          # كما تُعيده db.day_counts_map
    ok, why = trader.can_open("2026-01-01", "DAY", "SOLUSDT",
                              open_positions=[], day_counts=counts)
    assert not ok and why == "daily-limit", f"الحد اليومي لم يُطبَّق: {ok},{why}"

    ok2, _ = trader.can_open("2026-01-01", "DAY", "XRPUSDT",
                             open_positions=[], day_counts=counts)
    assert ok2, "رمز بلا صفقات يجب أن يُسمح له"

    # حدّ المراكز المتزامنة
    pos = [{"symbol": s, "system": "DAY"} for s in ("A", "B", "C", "D", "E", "F")]
    ok3, why3 = trader.can_open("2026-01-01", "DAY", "SOLUSDT",
                                open_positions=pos, day_counts={})
    assert not ok3 and why3 == "max-concurrent"
