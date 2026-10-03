"""
محرك التنفيذ المشترك (Shared Execution Engine).

القاعدة الذهبية: **نفس هذا الكود** يخدم الباك تست والورقي (ولاحقاً الحقيقي).
أي اختلاف بين المسارات = انحياز في النتائج. لذلك:

  * لا يُسمح بتقييم أي خروج على بيانات سابقة لـ entry_time  (منع "السفر في الزمن").
  * الدخول يُسعَّر على السعر القابل للتنفيذ لحظة التنفيذ (ask للشراء / bid للبيع).
  * عند لمس الوقف والهدف في الشمعة نفسها → نفترض الأسوأ (الوقف أولاً).
  * الفجوة السعرية (gap) تُنفَّذ عند سعر الافتتاح لا عند سعر الوقف.
  * رسوم التمويل (funding) تُحسب على كل مدة احتفاظ تامة (8 ساعات) في العقود الدائمة.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from . import config

# ────────────────────────────── الثوابت ──────────────────────────────
FUNDING_INTERVAL_HOURS = 8.0
PERPETUAL = True  # هذه عقود دائمة (USDT-M) → يترتب عليها تمويل

# تكلفة تنفيذ مُجمّعة (سبريد + انزلاق) بوحدة bps تُستخدم في **المحاكاة فقط**،
# لأن المحاكي لا يملك دفتر أوامر تاريخي. في النظام الحي يُحسب السبريد فعلياً
# من bookTicker والانزلاق من عمق السوق.
SLIPPAGE_SIM_BPS = float(config._fenv("SLIPPAGE_SIM_BPS", 2.0))


# ────────────────────────────── الأكواد المساعدة ──────────────────────────────
def spread_bps(bid: float, ask: float) -> float:
    """السبريد بوحدة نقطة أساس (bps)."""
    if not bid or not ask:
        return 0.0
    return (ask - bid) / ((ask + bid) / 2.0) * 10_000.0


def exec_price(side: int, bid: float, ask: float) -> float:
    """السعر القابل للتنفيذ فوراً: تشتري على ask وتبيع على bid (تدفع السبريد)."""
    return ask if side == 1 else bid


def slippage_bps(notional: float, liquidity_usd: float) -> float:
    """
    انزلاق يتناسب مع حجم الصفقة مقابل السيولة المتاحة، بدل رقم ثابت.
    كلما كبرت الصفقة مقابل عمق السوق، دفعت أكثر.
    يُقفل عند [min, max] من الإعدادات حتى لا يخرج عن المعقول.
    """
    base = config.SLIPPAGE["base_bps"]
    if liquidity_usd <= 0:
        return config.SLIPPAGE["max_bps"]
    impact = config.SLIPPAGE["impact_k"] * (notional / liquidity_usd) * 10_000.0
    return min(max(base + impact, config.SLIPPAGE["min_bps"]), config.SLIPPAGE["max_bps"])


def apply_cost(px: float, side: int, bps: float) -> float:
    """يطبّق تكلفة (انزلاق/عمولة) في اتجاه يضرك دائماً."""
    return px * (1.0 + bps / 10_000.0) if side == 1 else px * (1.0 - bps / 10_000.0)


def plan_exit(side: int, entry_px: float, atr: float, tp_atr: float, sl_atr: float):
    """يحسب الهدف والوقف انطلاقاً من **سعر التنفيذ الفعلي** لا من إغلاق الشمعة."""
    tp = entry_px + side * tp_atr * atr if tp_atr else 0.0
    sl = entry_px - side * sl_atr * atr
    return tp, sl


def fill_stop(side: int, sl: float, open_px: float) -> float:
    """
    الفجوة السعرية: إذا افتتحت الشمعة beyond الوقف فالتنفيذ يحدث عند الافتتاح،
    لا عند سعر الوقف (الوقف market order وليس ضماناً سعرياً).
    """
    if side == 1 and open_px < sl:
        return open_px
    if side == -1 and open_px > sl:
        return open_px
    return sl


def evaluate_bar(side: int, tp: float, sl: float,
                 o: float, h: float, l: float, c: float,
                 worst_case: bool = True):
    """
    يحدد هل ضُرب الوقف أو الهدف داخل شمعة واحدة — وبأي سعر نُفّذ.

    worst_case=True  → عند لمس الاثنين في الشمعة نفسها نفترض **الوقف** أولاً.
    هذا متحفظ عن قصد: لا يمكن معرفة الترتيب الحقيقي من شمعة واحدة،
    وافتراض الأسوأ يجعل الباك تست والورقي متحفظين بدل أن يكونا متفائلين.
    """
    sl_hit = (l <= sl) if side == 1 else (h >= sl)
    # ⚠️ الحارس tp > 0 ضروري: الساق B في FALCON بلا هدف (tp = 0)،
    # وبدونه يصبح (high >= 0) صحيحاً دائماً فتُغلق الصفقة بسعر صفر.
    tp_hit = bool(tp and ((h >= tp) if side == 1 else (l <= tp)))

    if sl_hit and tp_hit:
        return ("SL", fill_stop(side, sl, o)) if worst_case else ("TP", tp)
    if sl_hit:
        return "SL", fill_stop(side, sl, o)
    if tp_hit:
        return "TP", tp          # الهدف أمر LIMIT → يُملأ عند مستواه
    return None


def funding_cost(notional: float, hours_held: float, rate_per_8h: float) -> float:
    """
    تكلفة التمويل للعقود الدائمة: تُدفع كل 8 ساعات على القيمة الاسمية.
    rate>0 → يدفع الطويل للقصير؛ rate<0 → العكس.
    """
    if not PERPETUAL or not rate_per_8h or hours_held <= 0:
        return 0.0
    periods = hours_held / FUNDING_INTERVAL_HOURS
    return notional * rate_per_8h * periods


def trade_pnl(side: int, entry: float, exit_px: float, qty: float,
              fee_bps: float, funding: float = 0.0) -> float:
    """صافي الربح/الخسارة بعد العمولة (على الجانبين) والتمويل."""
    gross = side * (exit_px - entry) * qty
    fees = (entry * qty + exit_px * qty) * (fee_bps / 10_000.0)
    return gross - fees - funding


# ────────────────────────────── الصفقة ──────────────────────────────
@dataclass
class SimTrade:
    """صفقة في المحاكي — نفس الحقول التي يكتبها النظام الحي."""
    symbol: str
    system: str
    side: int
    leg: str = ""
    entry_time: Optional[datetime] = None
    entry: float = 0.0
    qty: float = 0.0
    tp: float = 0.0
    sl: float = 0.0
    atr: float = 0.0
    hold_hours: float = 24.0
    trail_atr: float = 0.0
    exit_time: Optional[datetime] = None
    exit_price: float = 0.0
    reason: str = ""
    net: float = 0.0
    r: float = 0.0
    fee: float = 0.0
    funding: float = 0.0
    mfe: float = 0.0          # أقصى ربح عائم (للتشخيص)
    mae: float = 0.0          # أقصى خسارة عائمة
    entry_slip_bps: float = 0.0
    entry_spread_bps: float = 0.0
    bars_held: int = 0
    immediate: bool = False   # هل أُغلقت في نفس شمعة الدخول؟ (يجب أن يبقى 0)

    def close(self, exit_px: float, reason: str, ts: datetime,
              fee_bps: float, funding_rate: float = 0.0):
        hours = (ts - self.entry_time).total_seconds() / 3600.0 if ts and self.entry_time else 0.0
        notional = self.entry * self.qty
        self.funding = funding_cost(notional, hours, funding_rate)
        self.exit_time = ts
        self.exit_price = exit_px
        self.reason = reason
        self.fee = (self.entry + exit_px) * self.qty * (fee_bps / 10_000.0)
        self.net = trade_pnl(self.side, self.entry, exit_px, self.qty,
                             fee_bps, self.funding)
        risk = abs(self.entry - self.sl) * self.qty
        self.r = (self.net / risk) if risk else 0.0
        return self


# ────────────────────────────── حلقة الإدارة ──────────────────────────────
def monitor(t, bars, fee_bps, funding_rate=0.0, worst_case=True,
            trail_atr=0.0, start_ts=None):
    """
    يمرّر الصفقة على سلسلة شموع **تبدأ بعد لحظة الدخول فقط**.

    start_ts: لحظة الدخول. أي شمعة تُغلق قبلها تُتجاهل تماماً —
    هذا هو الإصلاح الجوهري الذي يمنع "الفتح والإغلاق الفوري".

    الوقف المتحرك (ساق B): يرتفع مع القمة/القاع ولا ينزل أبداً.
    """
    if t.entry_time is None:
        start_ts = None
    sl = t.sl
    tp = t.tp
    extreme = t.entry          # لتتبع الوقف المتحرك
    worst_equity = 0.0
    best_equity = 0.0

    for ts, o, h, l, c in bars:
        # ❗ الشرط الحاسم: لا نقيّم شيئاً قبل لحظة الدخول.
        # المقارنة صارمة (<) لأن الشمعة التي تفتح في لحظة الدخول نفسها
        # لاحقة للدخول بالكامل ويجب أن تُحسب (وإلا فقدنا شمعة كاملة = تحيّز).
        if start_ts is not None and ts < start_ts:
            continue
        t.bars_held += 1

        # تتبع الأرباح/الخسائر العائمة (تشخيصي)
        eq = t.side * (c - t.entry) * t.qty
        best_equity = max(best_equity, eq)
        worst_equity = min(worst_equity, eq)

        # وقف متحرك (ساق B)
        if t.leg == "B" and trail_atr and t.atr:
            extreme = max(extreme, h) if t.side == 1 else min(extreme, l)
            cand = extreme - trail_atr * t.atr if t.side == 1 else extreme + trail_atr * t.atr
            sl = max(sl, cand) if t.side == 1 else min(sl, cand)

        hit = evaluate_bar(t.side, tp, sl, o, h, l, c, worst_case=worst_case)
        if hit:
            reason, xp = hit
            if reason == "SL" and sl != t.sl:
                reason = "TSL"
            t.mfe, t.mae = best_equity, worst_equity
            return t.close(xp, reason, ts, fee_bps, funding_rate)

        # خروج زمني
        hours = (ts - t.entry_time).total_seconds() / 3600.0
        if hours >= t.hold_hours:
            t.mfe, t.mae = best_equity, worst_equity
            return t.close(c, "TIME", ts, fee_bps, funding_rate)

    t.mfe, t.mae = best_equity, worst_equity
    return t          # ما زالت مفتوحة


# ────────────────────────────── ملخص الأداء ──────────────────────────────
def stats(trades):
    closed = [t for t in trades if t.reason]
    if not closed:
        return dict(n=0, wins=0, losses=0, wr=0.0, pf=0.0, net=0.0,
                    avg_r=0.0, immediate=0, avg_hold_h=0.0)
    wins = [t.net for t in closed if t.net > 0]
    loss = [-t.net for t in closed if t.net <= 0]
    g, l = sum(wins), sum(loss)
    held = [(t.exit_time - t.entry_time).total_seconds() / 3600.0
            for t in closed if t.exit_time and t.entry_time]
    return dict(
        n=len(closed),
        wins=len(wins), losses=len(loss),
        wr=round(len(wins) / len(closed) * 100, 1),
        pf=round(g / l, 2) if l > 0 else 0.0,
        net=round(sum(t.net for t in closed), 2),
        avg_r=round(sum(t.r for t in closed) / len(closed), 3),
        immediate=sum(1 for t in closed if t.immediate),
        avg_hold_h=round(sum(held) / len(held), 2) if held else 0.0,
        reasons={r: sum(1 for t in closed if t.reason == r)
                 for r in sorted({t.reason for t in closed})},
    )
