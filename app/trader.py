"""
التداول الورقي: تحجيم + تسعير + فتح + إدارة TP/SL/زمني/متحرك.

الإصلاحات الجوهرية هنا (انظر app/exec.py للمحرك المشترك):

  1. لا يُقيَّم أي خروج قبل `entry_time`           ← يمنع "الفتح والإغلاق الفوري".
  2. الدخول يُسعَّر على ask/bid الفعلي + انزلاق    ← سعر تنفيذ حقيقي لا إغلاق شمعة.
  3. عند لمس الوقف والهدف معاً نفترض الأسوأ (الوقف) ← تحفّظ متسق مع الباك تست.
  4. الفجوة السعرية تُنفَّذ عند السعر المتاح       ← الوقف ليس ضماناً سعرياً.
  5. رسوم التمويل تُحسب على مدة الاحتفاظ           ← ضروري لـ FALCON (حتى 25 يوماً).
"""
from datetime import datetime, timedelta, timezone

from . import config, db, exec as ex

RISK = config.RISK


def _tz(dt):
    return dt if (dt is not None and dt.tzinfo) else (
        dt.replace(tzinfo=timezone.utc) if dt else None)


# ────────────────────────────── التسعير ──────────────────────────────
def quote_entry(market, symbol, side, notional):
    """
    يحسب سعر الدخول القابل للتنفيذ فعلاً:
      الشراء على ask، البيع على bid، زائد انزلاق يتناسب مع حجم الصفقة والسيولة.
    """
    bk = market.book(symbol)
    raw = ex.exec_price(side, bk["bid"], bk["ask"])
    depth_usd = (bk["ask_qty"] * bk["ask"]) if side == 1 else (bk["bid_qty"] * bk["bid"])
    bps = ex.slippage_bps(notional, depth_usd)
    return ex.apply_cost(raw, side, bps), dict(
        spread_bps=round(ex.spread_bps(bk["bid"], bk["ask"]), 3),
        slip_bps=round(bps, 3), bid=bk["bid"], ask=bk["ask"])


# ────────────────────────────── التحجيم ──────────────────────────────
def position_size(equity, risk_pct, sl_dist, price,
                  max_notional_pct=None, min_notional=None):
    max_notional_pct = max_notional_pct or RISK["max_notional_pct"]
    min_notional = min_notional or RISK["min_notional_usd"]
    if sl_dist <= 0 or price <= 0:
        return 0.0
    qty = (equity * risk_pct) / sl_dist
    qty = min(qty, (equity * max_notional_pct) / price)
    if qty * price < min_notional:
        return 0.0
    return qty


def can_open(day, system, symbol, open_positions=None, day_counts=None):
    """
    نسخة مجمّعة: تستقبل المراكز والعدادات جاهزة بدل استعلام لكل رمز (N+1).
    """
    maxd = (config.DAY["max_per_day"] if system == "DAY"
            else config.FALCON.get("max_per_day", 3))
    if day_counts is not None:
        n = day_counts.get((system, symbol), 0)
    else:
        n = db.day_count(day, system, symbol)
    if n >= maxd:
        return False, "daily-limit"
    if open_positions is None:
        open_positions = db.open_positions()
    if len(open_positions) >= RISK["max_concurrent"]:
        return False, "max-concurrent"
    return True, "ok"


def check_halts(equity, day_start_equity, peak):
    """يبقى كما هو — لكنه الآن يُستدعى بقيمة صحيحة بعد إصلاح ترتيب القراءة."""
    if RISK.get("daily_loss_halt", 0) > 0 and day_start_equity and \
            (equity / day_start_equity - 1) <= -RISK["daily_loss_halt"]:
        return "daily-loss-halt"
    if RISK.get("max_drawdown_halt", 0) > 0 and peak and \
            (equity / peak - 1) <= -RISK["max_drawdown_halt"]:
        return "max-drawdown-halt"
    return None


# ────────────────────────────── الفتح ──────────────────────────────
def place_entry(system, sym, side, qty, px, tp, sl, day, hold_hours, leg="",
                trail_atr=0.0, atr_now=0.0, reason_ar="", bump=True, signal_key="",
                meta=None):
    meta = meta or {}
    tid = db.open_trade(system, sym, side, px, qty, tp or 0, sl, leg,
                        trail_atr, atr_now, hold_hours, day, reason_ar, signal_key,
                        meta=meta)
    if tid is None:
        return None
    if bump:
        db.bump_day(day, system, sym)
    db.log_event("ORDER",
                 f"{system} {sym} {'LONG' if side > 0 else 'SHORT'}{leg} "
                 f"qty={qty:.6f} @{px} tp={tp} sl={sl} "
                 f"spread={meta.get('spread_bps')}bps slip={meta.get('slip_bps')}bps")
    return dict(id=tid, system=system, symbol=sym, side=side, entry=px, qty=qty,
                tp=tp or 0, sl=sl, leg=leg, reason_ar=reason_ar)


# ────────────────────────────── الإدارة ──────────────────────────────
def manage_all(market, live_prices=None):
    """إدارة كل المراكز المفتوحة — تعيد قائمة الصفقات المغلقة (للإشعارات)."""
    closed = []
    for t in db.open_positions():
        try:
            hit = manage_one(market, t, live_prices)
            if hit:
                closed.append(hit)
        except Exception as e:
            db.log_event("ERR", f"manage {t['id']}: {e}")
    return closed


def _trailing_sl(market, t, sl0, atr0):
    """وقف متحرك لساق B: يرتفع مع القمة (أو ينخفض مع القاع) ولا يتراجع أبداً."""
    leg = t.get("leg") or ""
    trail_atr = float(t.get("trail_atr") or 0)
    if leg != "B" or trail_atr <= 0 or atr0 <= 0:
        return sl0, t.get("sl_current")
    entry_t = _tz(t["entry_time"])
    try:
        dfh = market.klines(t["symbol"], "4h", limit=60)
        # ❗ فقط الشموع اللاحقة للدخول — لا نقرأ ما قبل وجود الصفقة
        dfh = dfh[dfh["open_time"] >= entry_t]
        if len(dfh):
            ext = float(dfh["high"].max()) if t["side"] == 1 else float(dfh["low"].min())
            cand = ext - trail_atr * atr0 if t["side"] == 1 else ext + trail_atr * atr0
            new = max(sl0, cand) if t["side"] == 1 else min(sl0, cand)
            return new, new
    except Exception:
        pass
    return sl0, t.get("sl_current")


def _closed_candle_view(market, t, sl, tp):
    """
    بديل عند تعذّر الحصول على سعر حي: يقيّم الشموع المكتملة —
    لكن **فقط** تلك التي أُغلقت بعد لحظة الدخول.
    """
    entry_t = _tz(t["entry_time"])
    try:
        df = market.klines(t["symbol"], "15m", limit=6)
    except Exception:
        return None
    df = df[df["open_time"] + timedelta(minutes=15) > entry_t]
    if df.empty:
        return None                       # لم يمرّ وقت كافٍ بعد الدخول
    last = df.iloc[-1]
    hit = ex.evaluate_bar(t["side"], tp, sl,
                          float(last["open"]), float(last["high"]),
                          float(last["low"]), float(last["close"]),
                          worst_case=True)
    return hit


def manage_one(market, t, live_prices=None):
    """
    يدير مركزاً واحداً.

    live_prices: قاموس {symbol: last_price} من حلقة المراقبة السريعة.
    عند توفّره نستخدم السعر الحي (أدق)، وإلا نسقط على الشموع المكتملة
    مع شرط صارم: لا نقيّم إلا ما بعد `entry_time`.
    """
    sym, side = t["symbol"], t["side"]
    entry_t = _tz(t["entry_time"])
    now = datetime.now(timezone.utc)

    # ❗ الحاجز الزمني: لا يمكن إغلاق صفقة قبل أن تُفتح (كان هذا هو الخلل الأكبر)
    if entry_t and now <= entry_t:
        return None

    sl0, tp = float(t["sl"]), float(t["tp"])
    atr0 = float(t.get("atr0") or 0)
    sl, sl_current = _trailing_sl(market, t, sl0, atr0)
    if sl_current is not None and sl != sl0:
        try:
            db.update_sl(t["id"], sl)     # نحفظ الوقف المتحرك للتدقيق
        except Exception:
            pass

    hit = None
    live = (live_prices or {}).get(sym)

    if live:
        # ── المسار الدقيق: السعر الحي ──
        # الترتيب متحفّظ: الوقف أولاً (إن ضُربا معاً نفترض الأسوأ)
        if side == 1:
            if live <= sl:
                hit = ("TSL" if sl > sl0 else "SL", min(live, sl))   # فجوة → السعر المتاح
            elif tp > 0 and live >= tp:
                hit = ("TP", tp)                                      # أمر LIMIT
        else:
            if live >= sl:
                hit = ("TSL" if sl < sl0 else "SL", max(live, sl))
            elif tp > 0 and live <= tp:
                hit = ("TP", tp)
        exit_px_live = live
    else:
        # ── المسار الاحتياطي: آخر شمعة مكتملة بعد الدخول ──
        r = _closed_candle_view(market, t, sl, tp)
        if r:
            hit = r
        exit_px_live = None

    if hit is None:
        age_h = (now - entry_t).total_seconds() / 3600
        if age_h >= float(t.get("hold_hours") or 24):
            px = live or exit_px_live
            if px is None:
                try:
                    px = float(market.klines(sym, "15m", limit=3).iloc[-1]["close"])
                except Exception:
                    return None
            hit = ("TIME", px)
        else:
            return None

    reason, xp = hit
    qty = float(t["qty"])
    fee_bps = config.FEE_BPS
    hours = (now - entry_t).total_seconds() / 3600
    rate = config.FUNDING_RATE_8H if config.FUNDING_ENABLED else 0.0
    funding = ex.funding_cost(float(t["entry"]) * qty, hours, rate)
    net = ex.trade_pnl(side, float(t["entry"]), xp, qty, fee_bps, funding)
    fee = (float(t["entry"]) * qty + xp * qty) * (fee_bps / 10_000.0)
    risk_usd = qty * abs(float(t["entry"]) - sl0) if t["sl"] else 1
    r = net / risk_usd if risk_usd else 0

    db.close_trade(t["id"], xp, reason, round(net, 2), round(r, 3),
                   round(fee, 2), round(funding, 4))
    db.log_event("EXIT", f"{t['system']} {sym} {reason} @{xp} net={net:.2f} "
                         f"funding={funding:.2f} held={hours:.1f}h")
    out = dict(t)
    out.update(status="CLOSED", exit_price=xp, reason=reason, net=round(net, 2),
               r=round(r, 3), fee=round(fee, 2))
    return out
