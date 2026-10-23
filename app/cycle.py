"""
الدورة: مسح → فتح → إدارة → summary.

الإصلاحات هنا:
  * القرار يبقى على الشمعة المكتملة (صحيح أصلاً)، لكن **التنفيذ على السعر الحي**
    عبر trader.quote_entry (ask/bid + انزلاق) بدل إغلاق الشمعة.
  * إصلاح منطق الإيقاف: كانت الحالة تُمسح قبل أن تُقرأ، فلا تمنع الفتح أبداً.
  * استعلامات مجمّعة: المراكز المفتوحة وعدّادات اليوم تُقرأ مرة واحدة لا لكل رمز.
  * احترام ENABLE_DAY / ENABLE_FALCON لإيقاف نظام دون حذف كوده.
"""
import time
import traceback
from collections import Counter
from datetime import datetime, timezone, timedelta

from . import config, db, notify, trader, strategy as st
from .cache import cache
from .market import VisionMarket

DAY_AR = {1: "ارتداد + BTC هادئ + RSI + فلتر الساعة ✅",
          -1: "انعكاس هابط + BTC هادئ + RSI + فلتر الساعة ✅"}
FALCON_AR = "اختراق Donchian + زخم + نظام صاعد ✅"


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _bars(md, sym, tf, need):
    df = md.klines(sym, tf, limit=need)
    return df.iloc[:-1].reset_index(drop=True)      # إسقاط الشمعة المتشكلة


def run_cycle(note=""):
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    summ = dict(scanned_day=0, scanned_falcon=0, scanned_sniper=0,
                signals=0, opened=0, closed=0,
                signals_day=0, signals_falcon=0, signals_sniper=0,
                opened_day=0, opened_falcon=0, opened_sniper=0,
                closed_day=0, closed_falcon=0, closed_sniper=0,
                health={}, errors=[], reject_day={}, reject_falcon={}, reject_sniper={})
    if not cache.acquire("ct:cycle:lock", ttl=300):
        summ["errors"].append("lock-busy: دورة سابقة ما زالت تعمل")
        summ["health"] = {"cycle": "skip"}
        return _finish(summ, started, note or "lock-skip", t0)
    try:
        md = VisionMarket()
        # ── 1) نبض المحركات ──
        try:
            md.price("BTCUSDT")
            summ["health"]["binance"] = md.provider_status()
        except Exception as e:
            summ["health"]["binance"] = f"ERR {e}"[:120]
            summ["errors"].append(f"binance: {e}"[:150])
            return _finish(summ, started, note, t0)
        try:
            summ["health"]["supabase"] = "ok" if db.health() else "ERR"
        except Exception as e:
            summ["health"]["supabase"] = f"ERR {e}"[:120]
            summ["errors"].append(f"supabase: {e}"[:150])
            return _finish(summ, started, note, t0)
        summ["health"]["redis"] = "ok" if cache.ping() else "ERR"
        if summ["health"]["redis"] != "ok":
            summ["errors"].append("redis: ping failed (fallback memory)")
        summ["health"]["telegram"] = "skip"
        if config.BOT_TOKEN:
            try:
                summ["health"]["telegram"] = "ok" if notify.get_me() else "ERR"
            except Exception as e:
                summ["health"]["telegram"] = f"ERR {e}"[:120]
        adm = config.ADMIN_CHAT_ID or db.get_state("admin_chat_id", "")

        def _send(text):
            if adm and config.BOT_TOKEN:
                ok, _ = notify.send_text(adm, text)
                if not ok:
                    summ["errors"].append("telegram: send failed")
            elif adm or config.BOT_TOKEN:
                summ["errors"].append("telegram: token/admin missing")

        # ── 2) أسعار → Redis (تخدم حلقة المراقبة السريعة) ──
        try:
            for s, v in md.prices(config.ALL_SYMBOLS).items():
                cache.set(f"ct:px:{s}", v, ex=300)
        except Exception as e:
            summ["errors"].append(f"prices-cache: {e}"[:120])

        # ── 3) يوم جديد + الرصيد الكلي (محقق + غير محقق) ──
        today = _today()
        open_pos = db.open_positions()
        try:
            live_for_eq = md.prices(sorted({p["symbol"] for p in open_pos}))
        except Exception:
            live_for_eq = {}
        equity = db.equity(live_for_eq)

        new_day = db.get_state("day", "") != today
        if new_day:
            db.set_state("day", today)
            db.set_state("day_start", str(equity))
            db.set_state("halted", "")
            db.log_event("INFO", f"new day {today} eq={equity:.1f}")
            try:
                db.prune_old(config._ienv("PRUNE_DAYS", 30))
            except Exception:
                pass
        day_start = float(db.get_state("day_start", str(equity)) or equity)

        # ✅ إصلاح: نقرأ حالة الإيقاف أولاً، ولا نُمسحها إلا عند بدء يوم جديد
        halted = db.get_state("halted", "") or None

        # ── 4) مسح + فتح (يُمنع في الإيقاف) ──
        if halted:
            summ["errors"].append(f"halted: {halted} (الفتح متوقف)")
        else:
            counts = db.day_counts_map(today, ["SNIPER", "DAY", "FALCON"])
            if getattr(config, "ENABLE_SNIPER", True):
                open_pos = _scan_sniper(md, today, equity, summ, _send, open_pos, counts)
            else:
                summ["reject_sniper"] = {"system-disabled": 1}
            if config.ENABLE_DAY:
                open_pos = _scan_day(md, today, equity, summ, _send, open_pos, counts)
            else:
                summ["reject_day"] = {"system-disabled": 1}
            if config.ENABLE_FALCON:
                open_pos = _scan_falcon(md, today, equity, summ, _send, open_pos, counts)
            else:
                summ["reject_falcon"] = {"system-disabled": 1}

        # ── 5) إدارة (تعمل دائماً حتى في الإيقاف — مع تمرير الأسعار الحية) ──
        open_positions = db.open_positions()
        live_manage = {}
        if open_positions:
            try:
                syms_manage = sorted({p["symbol"] for p in open_positions})
                live_manage = md.prices(syms_manage)
            except Exception:
                live_manage = {}

        # إغلاق وتصفية أي صفقات قديمة تتبع الأنظمة الملغاة (DAY و FALCON) لتنقية الحساب لمحرك SNIPER
        for p in list(open_positions):
            if p.get("system") in ("DAY", "FALCON"):
                try:
                    px_now = live_manage.get(p["symbol"]) or float(md.price(p["symbol"]))
                    tr_closed = trader.force_close(p, px_now, "SYSTEM_RETIRED")
                    _send(f"🧹 تصفية مركز سابق يتبع النظام الملغى ({p['system']}): {p['symbol']}\nتم الإغلاق بسعر {px_now} لتنقية المحفظة لمحرك SNIPER فقط.")
                except Exception as e:
                    summ["errors"].append(f"retire-legacy-{p['symbol']}: {e}"[:100])

        for tr in trader.manage_all(md, live_prices=live_manage):
            summ["closed"] += 1
            sys_name = tr.get("system", "")
            if sys_name == "SNIPER":
                summ["closed_sniper"] = summ.get("closed_sniper", 0) + 1
            elif sys_name == "DAY":
                summ["closed_day"] += 1
            else:
                summ["closed_falcon"] += 1
            _send(notify.t_close(tr))

        # ── 6) رصيد + إيقاف ──
        try:
            live2 = md.prices(sorted({p["symbol"] for p in db.open_positions()})) \
                or live_for_eq
        except Exception:
            live2 = live_for_eq
        equity = db.equity(live2)
        peak = db.mark_equity(equity)
        h = trader.check_halts(equity, day_start, peak)
        if h and not halted:
            db.set_state("halted", h)
            db.log_event("HALT", h)
            _send(notify.t_halt(h, equity))
        elif not h and halted:
            db.set_state("halted", "")        # تعافى
        summ["equity"] = equity
    except Exception as e:
        summ["errors"].append(f"cycle-crash: {e}"[:200])
        try:
            db.log_event("ERR", f"cycle: {e}\n{traceback.format_exc(limit=3)}")
        except Exception:
            pass
    finally:
        cache.release("ct:cycle:lock")
    return _finish(summ, started, note, t0)


def _finish(summ, started, note, t0=None):
    t0 = time.monotonic() if t0 is None else t0
    ended = datetime.now(timezone.utc)
    summ["duration_ms"] = int((time.monotonic() - t0) * 1000)
    try:
        scanned_primary = summ.get("scanned_sniper") or summ.get("scanned_day", 0)
        cycle_health = dict(summ["health"],
                            signals_sniper=summ.get("signals_sniper", 0),
                            opened_sniper=summ.get("opened_sniper", 0),
                            closed_sniper=summ.get("closed_sniper", 0),
                            signals_day=summ.get("signals_day", 0),
                            signals_falcon=summ.get("signals_falcon", 0),
                            opened_day=summ.get("opened_day", 0),
                            opened_falcon=summ.get("opened_falcon", 0),
                            closed_day=summ.get("closed_day", 0),
                            closed_falcon=summ.get("closed_falcon", 0))
        db.save_cycle(dict(started_at=started, ended_at=ended,
                           duration_ms=summ["duration_ms"],
                           scanned_day=scanned_primary,
                           scanned_falcon=summ["scanned_falcon"],
                           reject_day=summ.get("reject_sniper", summ.get("reject_day", {})),
                           reject_falcon=summ["reject_falcon"],
                           signals=summ["signals"], opened=summ["opened"],
                           closed=summ["closed"], health=cycle_health,
                           errors=summ["errors"][:10],
                           equity=summ.get("equity"), note=note))
    except Exception as e:
        print(f"[cycle-save-failed] {e}", flush=True)
    return summ


def _scan_sniper(md, today, equity, summ, send, open_pos, counts):
    p = config.SNIPER
    rej = Counter()
    symbols = getattr(config, "SNIPER_SYMBOLS", config.ALL_SYMBOLS)

    # فحص دورة 15m: الشموع المكتملة تتغير فقط كل 15 دقيقة (:00, :15, :30, :45)
    # لا داعي لمسح 32 شمعة كل 60 ثانية إذا لم تغلق شمعة جديدة حديثاً!
    now = datetime.now(timezone.utc)
    minute = now.minute
    candle_window = (minute % 15) <= 2
    last_scanned_candle = cache.get("ct:sniper:last_candle")
    current_candle_id = now.replace(minute=(minute // 15) * 15, second=0, microsecond=0).isoformat()

    if not candle_window and last_scanned_candle == current_candle_id:
        summ["reject_sniper"] = {"waiting-new-15m-candle": len(symbols)}
        return open_pos

    cache.set("ct:sniper:last_candle", current_candle_id, ex=1800)

    for sym_ in symbols:
        summ["scanned_sniper"] = summ.get("scanned_sniper", 0) + 1
        time.sleep(0.08)  # مهلة 80ms بين العملات لمنع ضغط الطلبات المفاجئ وحظر 418
        try:
            df = _bars(md, sym_, "15m", 250)
            side, px_sig, tp_sig, sl_sig, dbg = st.sniper_scan_debug(df, p)
            if dbg.get("reject"):
                rej[dbg["reject"]] += 1
            if side == 0:
                continue

            summ["signals"] += 1
            summ["signals_sniper"] = summ.get("signals_sniper", 0) + 1

            if any(x["symbol"] == sym_ and x.get("system") == "SNIPER" for x in open_pos):
                rej["anti-double"] += 1
                continue

            ok, why = trader.can_open(today, "SNIPER", sym_, open_pos, counts)
            if not ok:
                rej[f"risk-{why}"] += 1
                continue

            # 1. فحص حداثة الشمعة لـ SNIPER (15 دقيقة)
            candle_close_t = df.iloc[-1]["open_time"] + timedelta(minutes=15)
            signal_age_sec = (datetime.now(timezone.utc) - candle_close_t).total_seconds()
            if signal_age_sec > config.SIGNAL_MAX_AGE_SEC_DAY:
                rej["signal-stale"] += 1
                continue

            # 2. حجم المركز (مخاطرة 1% لكل صفقة)
            sl_dist = abs(px_sig - sl_sig)
            est_qty = trader.position_size(equity, p["risk_per_trade"], sl_dist, px_sig)
            if est_qty <= 0:
                rej["risk-size"] += 1
                continue

            # 3. التنفيذ على السعر الحي (ask/bid)
            px, meta = trader.quote_entry(md, sym_, side, est_qty * px_sig)
            if abs(px - px_sig) / px_sig > config.MAX_PRICE_DRIFT_PCT:
                rej["drift"] += 1
                continue

            actual_qty = trader.position_size(equity, p["risk_per_trade"], sl_dist, px)
            if actual_qty <= 0:
                rej["risk-size-live"] += 1
                continue

            tp_pct = float(p.get("tp_pct", 0.0075))
            sl_pct = float(p.get("sl_pct", 0.0500))
            live_tp = px * (1.0 + tp_pct) if side == 1 else px * (1.0 - tp_pct)
            live_sl = px * (1.0 - sl_pct) if side == 1 else px * (1.0 + sl_pct)

            signal_key = f"SNIPER:{sym_}:{side}:{df.iloc[-1]['open_time'].isoformat()}"
            tr = trader.place_entry(
                "SNIPER", sym_, side, actual_qty, px, live_tp, live_sl,
                today, hold_hours=0.0, leg="",
                reason_ar="قنص ارتدادي عالي الدقة (BB + RSI + EMA200) 🎯",
                signal_key=signal_key, meta=meta
            )
            if tr:
                open_pos.append(tr)
                counts[("SNIPER", sym_)] = counts.get(("SNIPER", sym_), 0) + 1
                summ["opened"] += 1
                summ["opened_sniper"] = summ.get("opened_sniper", 0) + 1
                send(notify.t_open(tr))
        except Exception as e:
            summ["errors"].append(f"SNIPER-{sym_}: {e}"[:120])
    summ["reject_sniper"] = dict(rej)
    return open_pos


def _scan_day(md, today, equity, summ, send, open_pos, counts):
    p = config.DAY
    try:
        anc = _bars(md, config.ANCHOR, "15m", 130)
        a_now = st.anchor_ar_now(anc)
    except Exception as e:
        summ["errors"].append(f"DAY-anchor: {e}"[:120])
        return open_pos
    rej = Counter()
    for sym_ in config.DAY_SYMBOLS:
        summ["scanned_day"] += 1
        try:
            df = _bars(md, sym_, "15m", 300)
            h1c, h1e = st.h1_now(df)
            side, px_sig, a, dbg = st.day_scan_debug(df, a_now, h1c, h1e, p)
            if dbg.get("reject"):
                rej[dbg["reject"]] += 1
            if side == 0:
                continue
            summ["signals"] += 1
            summ["signals_day"] += 1
            signal_key = f"DAY:{sym_}:{side}:{df.iloc[-1]['open_time'].isoformat()}"
            if any(x["symbol"] == sym_ and x["system"] == "DAY" for x in open_pos):
                rej["anti-double"] += 1
                continue
            ok, why = trader.can_open(today, "DAY", sym_, open_pos, counts)
            if not ok:
                rej[f"risk-{why}"] += 1
                continue

            # 1. فحص حداثة الشمعة: نرفض الإشارة إذا أغلقت الشمعة منذ وقت طويل
            candle_close_t = df.iloc[-1]["open_time"] + timedelta(minutes=15)
            signal_age_sec = (datetime.now(timezone.utc) - candle_close_t).total_seconds()
            if signal_age_sec > config.SIGNAL_MAX_AGE_SEC_DAY:
                rej["signal-stale"] += 1
                continue

            # ✅ التنفيذ على السعر الحي (ask/bid) لا على إغلاق الشمعة
            sl_d = p["sl_atr"] * a
            est_qty = trader.position_size(equity, p["risk_per_trade"], sl_d, px_sig)
            if est_qty <= 0:
                rej["risk-size"] += 1
                continue
            px, meta = trader.quote_entry(md, sym_, side, est_qty * px_sig)
            meta["entry_ref"] = float(px_sig)

            # 2. فحص انحراف السعر الحي عن سعر الإشارة (منع الشراء من القمة)
            drift_pct = abs(px - px_sig) / px_sig
            if drift_pct > config.MAX_PRICE_DRIFT_PCT:
                rej["price-drifted"] += 1
                continue

            qty = trader.position_size(equity, p["risk_per_trade"], abs(px - (px - side * sl_d)), px)
            if qty <= 0:
                rej["risk-size"] += 1
                continue

            tr = trader.place_entry("DAY", sym_, side, qty, px,
                                    px + side * p["tp_atr"] * a, px - side * sl_d,
                                    today, p["max_hold_bars"] * 0.25,
                                    reason_ar=DAY_AR[side], signal_key=signal_key,
                                    atr_now=a, meta=meta)
            if tr is None:
                rej["duplicate-signal"] += 1
                continue
            open_pos.append(dict(symbol=sym_, system="DAY", side=side))
            counts[("DAY", sym_)] = counts.get(("DAY", sym_), 0) + 1
            summ["opened"] += 1
            summ["opened_day"] += 1
            send(notify.t_open(tr))
        except Exception as e:
            summ["errors"].append(f"DAY-{sym_}: {e}"[:120])
    summ["reject_day"] = dict(rej)
    return open_pos


def _scan_falcon(md, today, equity, summ, send, open_pos, counts):
    p = config.FALCON
    rej = Counter()
    for sym_ in config.FALCON_SYMBOLS:
        summ["scanned_falcon"] += 1
        try:
            df = _bars(md, sym_, "4h", 260)
            dd = _bars(md, sym_, "1d", 250)
            dc, de50, de200 = st.daily_regime(dd)
            side, px_sig, a, dbg = st.falcon_scan_debug(df, dc, de50, de200, p)
            if dbg.get("reject"):
                rej[dbg["reject"]] += 1
            if side == 0:
                continue
            summ["signals"] += 1
            summ["signals_falcon"] += 1
            ok, why = trader.can_open(today, "FALCON", sym_, open_pos, counts)
            if not ok:
                rej[f"risk-{why}"] += 1
                continue
            if any(x["symbol"] == sym_ and x["system"] == "FALCON" for x in open_pos):
                rej["anti-double"] += 1
                continue

            # 1. فحص حداثة الشمعة لـ FALCON (4 ساعات)
            candle_close_t = df.iloc[-1]["open_time"] + timedelta(hours=4)
            signal_age_sec = (datetime.now(timezone.utc) - candle_close_t).total_seconds()
            if signal_age_sec > config.SIGNAL_MAX_AGE_SEC_FALCON:
                rej["signal-stale"] += 1
                continue

            signal_key = f"FALCON:{sym_}:{side}:{df.iloc[-1]['open_time'].isoformat()}"
            sl_d = p["sl_atr"] * a

            px, meta = trader.quote_entry(md, sym_, side,
                                          p["risk_per_trade"] * equity)
            meta["entry_ref"] = float(px_sig)

            # 2. فحص انحراف السعر الحي عن سعر الإشارة
            drift_pct = abs(px - px_sig) / px_sig
            if drift_pct > config.MAX_PRICE_DRIFT_PCT * 1.5:
                rej["price-drifted"] += 1
                continue

            sl_target = px - side * sl_d
            opened_any = False
            for leg, frac, tp in (("A", 0.5, px + side * p["tp_atr"] * a), ("B", 0.5, px + side * (p["tp_atr"] * 1.25) * a)):
                qty = trader.position_size(equity, p["risk_per_trade"] * frac, sl_d, px)
                if qty <= 0:
                    rej["risk-size"] += 1
                    continue
                tr = trader.place_entry("FALCON", sym_, side, qty, px, tp, sl_target,
                                        today, p["max_hold_bars"] * 4, leg=leg,
                                        trail_atr=p["trail_atr"] if leg == "B" else 0,
                                        atr_now=a, reason_ar=FALCON_AR, bump=(leg == "A"),
                                        signal_key=signal_key, meta=meta)
                if tr is None:
                    rej["duplicate-signal"] += 1
                    continue
                summ["opened"] += 1
                summ["opened_falcon"] += 1
                opened_any = True
                send(notify.t_open(tr))
            if opened_any:
                open_pos.append(dict(symbol=sym_, system="FALCON", side=side))
                counts[("FALCON", sym_)] = counts.get(("FALCON", sym_), 0) + 1
            else:
                db.log_event("WARN", f"FALCON {sym_}: فشل فتح أحد الساقين — مركز ناقص")
        except Exception as e:
            summ["errors"].append(f"FALCON-{sym_}: {e}"[:120])
    summ["reject_falcon"] = dict(rej)
    return open_pos


# ───────────────────── حلقة المراقبة السريعة (TP/SL بدقة ثوانٍ) ─────────────────────
def monitor_once():
    """
    تُستدعى كل MONITOR_INTERVAL_SEC ثانية. تقرأ الأسعار الحية للمراكز المفتوحة
    وتُقيّم TP/SL — أدق بكثير من انتظار إغلاق شمعة 15 دقيقة.

    تعيد قائمة الصفقات التي أُغلقت (للإشعار).
    """
    try:
        pos = db.open_positions()
        if not pos:
            return []
        md = VisionMarket()
        syms = sorted({p["symbol"] for p in pos})
        prices = md.prices(syms)
        for s, v in prices.items():
            cache.set(f"ct:px:{s}", v, ex=300)
        closed = trader.manage_all(md, live_prices=prices)
        return closed
    except Exception as e:
        try:
            db.log_event("ERR", f"monitor: {e}")
        except Exception:
            pass
        return []
