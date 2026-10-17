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
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except Exception:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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
    if system == "SNIPER":
        maxd = getattr(config, "SNIPER", {}).get("max_per_day", 4)
    elif system == "DAY":
        maxd = config.DAY["max_per_day"]
    else:
        maxd = config.FALCON.get("max_per_day", 3)
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
    positions = db.open_positions()
    if not positions:
        return closed
    # جلب الأسعار الحية تلقائياً للمراكز إن لم تُمرر
    if live_prices is None:
        try:
            syms = sorted({p["symbol"] for p in positions})
            live_prices = market.prices(syms)
        except Exception:
            live_prices = {}
    for t in positions:
        try:
            hit = manage_one(market, t, live_prices)
            if hit:
                closed.append(hit)
        except Exception as e:
            db.log_event("ERR", f"manage {t['id']}: {e}")
    return closed


def _trailing_sl(market, t, sl0, atr0, live=None):
    """وقف متحرك لساق B: يرتفع مع القمة (أو ينخفض مع القاع) ولا يتراجع أبداً."""
    leg = t.get("leg") or ""
    trail_atr = float(t.get("trail_atr") or 0)
    if leg != "B" or trail_atr <= 0 or atr0 <= 0:
        return sl0, t.get("sl_current")
    sl_curr = float(t.get("sl_current") or sl0)
    # تحديث لحظي بالسعر الحي إن توفر
    if live is not None and live > 0:
        if t["side"] == 1:
            cand = live - trail_atr * atr0
            if cand > sl_curr:
                return max(sl0, cand), max(sl0, cand)
        else:
            cand = live + trail_atr * atr0
            if cand < sl_curr:
                return min(sl0, cand), min(sl0, cand)
    entry_t = _tz(t["entry_time"])
    try:
        dfh = market.klines(t["symbol"], "4h", limit=60)
        # ❗ فقط الشموع المكتملة بعد الدخول — إسقاط المتشكلة وفلترة ما بعد الدخول
        dfh = dfh.iloc[:-1].reset_index(drop=True)
        dfh = dfh[dfh["open_time"] >= entry_t]
        if len(dfh):
            ext = float(dfh["high"].max()) if t["side"] == 1 else float(dfh["low"].min())
            cand = ext - trail_atr * atr0 if t["side"] == 1 else ext + trail_atr * atr0
            new = max(sl_curr, cand) if t["side"] == 1 else min(sl_curr, cand)
            return new, new
    except Exception:
        pass
    return sl_curr, sl_curr


def _closed_candle_view(market, t, sl, tp):
    """
    بديل عند تعذّر الحصول على سعر حي: يقيّم الشموع المكتملة —
    لكن **فقط** تلك التي بدأت وأُغلقت بالكامل بعد لحظة الدخول.
    """
    entry_t = _tz(t["entry_time"])
    try:
        df = market.klines(t["symbol"], "15m", limit=10)
    except Exception:
        return None
    # إسقاط الشمعة الحالية غير المكتملة لمنع قراءة بيانات قبل الدخول
    df = df.iloc[:-1].reset_index(drop=True)
    df = df[df["open_time"] >= entry_t]
    if df.empty:
        return None                       # لم تكتمل شمعة بعد لحظة الدخول
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
    يستخدم السعر اللحظي الحي حصراً لتقييم الهدف والوقف وتأمين الأرباح (Breakeven).
    """
    sym, side = t["symbol"], t["side"]
    entry_t = _tz(t["entry_time"])
    now = datetime.now(timezone.utc)

    # ❗ الحاجز الزمني: لا يمكن إغلاق صفقة قبل أن تُفتح
    if entry_t and now <= entry_t:
        return None

    entry_px = float(t["entry"])
    sl0, tp = float(t["sl"]), float(t["tp"])
    atr0 = float(t.get("atr0") or 0)

    # جلب السعر الحي من القاموس أو الكاش أو نداء مباشر للسوق
    live = (live_prices or {}).get(sym)
    if live is None:
        try:
            from .cache import cache
            cached = cache.get(f"ct:px:{sym}")
            if cached is not None:
                live = float(cached)
            else:
                live = float(market.price(sym))
        except Exception:
            live = None

    sl, sl_current = _trailing_sl(market, t, sl0, atr0, live=live)

    # ── تأمين المركز بنقطة التعادل (Breakeven) عند تحقيق 50% من الهدف ──
    if live is not None and config.BREAKEVEN_ENABLED and tp > 0:
        tp_dist = abs(tp - entry_px)
        if tp_dist > 0:
            progress = side * (live - entry_px)
            if progress >= config.BREAKEVEN_TRIGGER_RATIO * tp_dist:
                cand_be = entry_px
                if side == 1 and cand_be > sl:
                    sl = cand_be
                    sl_current = cand_be
                elif side == -1 and cand_be < sl:
                    sl = cand_be
                    sl_current = cand_be

    if sl_current is not None and sl != sl0:
        try:
            db.update_sl(t["id"], sl)     # نحفظ الوقف الجديد للتدقيق
        except Exception:
            pass

    hit = None
    exit_px_live = None

    if live is not None:
        # ── المسار الدقيق والرئيسي: السعر الحي ──
        if side == 1:
            if live <= sl:
                is_be = (sl >= entry_px - 1e-5 and sl > sl0)
                is_tsl = (sl > sl0 and not is_be)
                reason = "BE" if is_be else ("TSL" if is_tsl else "SL")
                fill_px = ex.apply_cost(sl, -side, config.SLIPPAGE.get("base_bps", 1.0))
                hit = (reason, fill_px)
            elif tp > 0 and live >= tp:
                hit = ("TP", tp)                # أمر LIMIT
        else:
            if live >= sl:
                is_be = (sl <= entry_px + 1e-5 and sl < sl0)
                is_tsl = (sl < sl0 and not is_be)
                reason = "BE" if is_be else ("TSL" if is_tsl else "SL")
                fill_px = ex.apply_cost(sl, -side, config.SLIPPAGE.get("base_bps", 1.0))
                hit = (reason, fill_px)
            elif tp > 0 and live <= tp:
                hit = ("TP", tp)
        exit_px_live = live
    else:
        # ── المسار الاحتياطي: فقط في حال انقطاع الشبكة كلياً عن السعر الحي ──
        # مع شرط صارم: الشمعة المكتملة بدأت بعد الدخول
        r = _closed_candle_view(market, t, sl, tp)
        if r:
            hit = r

    if hit is None:
        hold_h = float(t.get("hold_hours") or 0.0)
        # فقط إن كانت hold_hours محددة وموجبة يُطبّق انتهاء الوقت — لنظام SNIPER تكون 0 لمنع الإغلاق العشوائي
        if hold_h > 0:
            age_h = (now - entry_t).total_seconds() / 3600
            if age_h >= hold_h:
                px = live or exit_px_live
                if px is None:
                    try:
                        px = float(market.klines(sym, "15m", limit=3).iloc[-1]["close"])
                    except Exception:
                        return None
                hit = ("TIME", px)
            else:
                return None
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


def force_close(t, xp, reason="FORCE_CLOSE"):
    """إغلاق فوري لمركز محدد لإنهاء الأنظمة الملغاة أو أوامر الطوارئ."""
    now = datetime.now(timezone.utc)
    entry_t = _tz(t.get("entry_time")) or now
    side = t["side"]
    qty = float(t["qty"])
    sl0 = float(t.get("sl") or 0)
    fee_bps = config.FEE_BPS
    hours = max(0.0, (now - entry_t).total_seconds() / 3600.0)
    rate = config.FUNDING_RATE_8H if config.FUNDING_ENABLED else 0.0
    funding = ex.funding_cost(float(t["entry"]) * qty, hours, rate)
    net = ex.trade_pnl(side, float(t["entry"]), xp, qty, fee_bps, funding)
    fee = (float(t["entry"]) * qty + xp * qty) * (fee_bps / 10_000.0)
    risk_usd = qty * abs(float(t["entry"]) - sl0) if t.get("sl") else 1
    r = net / risk_usd if risk_usd else 0

    db.close_trade(t["id"], xp, reason, round(net, 2), round(r, 3),
                   round(fee, 2), round(funding, 4))
    db.log_event("EXIT", f"{t['system']} {t['symbol']} {reason} @{xp} net={net:.2f}")
    out = dict(t)
    out.update(status="CLOSED", exit_price=xp, reason=reason, net=round(net, 2),
               r=round(r, 3), fee=round(fee, 2))
    return out
