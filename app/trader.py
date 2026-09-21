"""التداول الورقي: تحجيم + حدود + فتح + إدارة TP/SL/زمني/متحرك (نفس منطق البوت المُثبت)."""
from datetime import datetime, timezone
from . import config, db

RISK = config.RISK


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


def can_open(day, system, symbol):
    maxd = config.DAY["max_per_day"] if system == "DAY" else 3
    if db.day_count(day, system, symbol) >= maxd:
        return False, "daily-limit"
    if len(db.open_positions()) >= RISK["max_concurrent"]:
        return False, "max-concurrent"
    return True, "ok"


def check_halts(equity, day_start_equity, peak):
    if RISK.get("daily_loss_halt", 0) > 0 and day_start_equity and (equity / day_start_equity - 1) <= -RISK["daily_loss_halt"]:
        return "daily-loss-halt"
    if RISK.get("max_drawdown_halt", 0) > 0 and peak and (equity / peak - 1) <= -RISK["max_drawdown_halt"]:
        return "max-drawdown-halt"
    return None


def place_entry(system, sym, side, qty, px, tp, sl, day, hold_hours, leg="",
                trail_atr=0.0, atr_now=0.0, reason_ar="", bump=True, signal_key=""):
    tid = db.open_trade(system, sym, side, px, qty, tp or 0, sl, leg,
                        trail_atr, atr_now, hold_hours, day, reason_ar, signal_key)
    if tid is None:
        return None
    if bump:
        db.bump_day(day, system, sym)
    db.log_event("ORDER",
                 f"{system} {sym} {'LONG' if side > 0 else 'SHORT'}{leg} "
                 f"qty={qty:.6f} @{px} tp={tp} sl={sl}")
    return dict(id=tid, system=system, symbol=sym, side=side, entry=px, qty=qty,
                tp=tp or 0, sl=sl, leg=leg, reason_ar=reason_ar)


def manage_all(market):
    """إدارة كل المراكز المفتوحة — تعيد قائمة الصفقات المغلقة (للإشعارات)."""
    closed = []
    for t in db.open_positions():
        try:
            hit = manage_one(market, t)
            if hit:
                closed.append(hit)
        except Exception as e:
            db.log_event("ERR", f"manage {t['id']}: {e}")
    return closed


def manage_one(market, t):
    sym, side = t["symbol"], t["side"]
    try:
        df = market.klines(sym, "15m", limit=3)
        last = df.iloc[-2]  # آخر شمعة مغلقة
        px = float(last["close"])
        hh = float(last["high"])
        ll = float(last["low"])
    except Exception:
        return None
    entry_t = t["entry_time"]
    if entry_t.tzinfo is None:
        entry_t = entry_t.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - entry_t).total_seconds() / 3600
    leg = t.get("leg") or ""
    trail_atr = float(t.get("trail_atr") or 0)
    atr0 = float(t.get("atr0") or 0)
    hold_h = float(t.get("hold_hours") or 24)
    sl, tp = float(t["sl"]), float(t["tp"])
    # وقف متحرك لساق B
    if leg == "B" and trail_atr > 0 and atr0 > 0:
        try:
            dfh = market.klines(sym, "4h", limit=40)
            recent = dfh[dfh["open_time"] >= entry_t]
            if len(recent):
                ext = float(recent["high"].max()) if side == 1 else float(recent["low"].min())
                nts = ext - trail_atr * atr0 if side == 1 else ext + trail_atr * atr0
                sl = max(sl, nts) if side == 1 else min(sl, nts)
        except Exception:
            pass
    hit = None
    if side == 1:
        sh = ll <= sl
        th = (tp > 0 and hh >= tp)
        if sh:
            hit = ("TSL" if sl > float(t["sl"]) else "SL", sl)
        elif th:
            hit = ("TP", tp)
    else:
        sh = hh >= sl
        th = (tp > 0 and ll <= tp)
        if sh:
            hit = ("TSL" if sl < float(t["sl"]) else "SL", sl)
        elif th:
            hit = ("TP", tp)
    if hit is None and age_h >= hold_h:
        hit = ("TIME", px)
    if hit is None:
        return None
    reason, xp = hit
    fee = (float(t["entry"]) * float(t["qty"]) + xp * float(t["qty"])) * (
        RISK["fee_side"] + RISK["slip_side"])
    net = side * (xp - float(t["entry"])) * float(t["qty"]) - fee
    risk_usd = float(t["qty"]) * abs(float(t["entry"]) - float(t["sl"])) if t["sl"] else 1
    r = net / risk_usd if risk_usd else 0
    db.close_trade(t["id"], xp, reason, round(net, 2), round(r, 3), round(fee, 2))
    db.log_event("EXIT", f"{t['system']} {sym} {reason} @{xp} net={net:.2f}")
    out = dict(t)
    out.update(status="CLOSED", exit_price=xp, reason=reason, net=round(net, 2),
               r=round(r, 3), fee=round(fee, 2))
    return out
