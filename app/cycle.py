"""الدورة: مسح → فتح → إدارة → summary — كل SCAN_INTERVAL_SEC."""
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
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
    return df.iloc[:-1].reset_index(drop=True)  # إسقاط المتشكلة


def run_cycle(note=""):
    t0 = time.time()
    started = datetime.now(timezone.utc)
    summ = dict(scanned_day=0, scanned_falcon=0, signals=0, opened=0, closed=0,
                signals_day=0, signals_falcon=0, opened_day=0, opened_falcon=0,
                closed_day=0, closed_falcon=0,
                health={}, errors=[], reject_day={}, reject_falcon={})
    if not cache.acquire("ct:cycle:lock", ttl=180):
        summ["errors"].append("lock-busy: دورة سابقة ما زالت تعمل")
        summ["health"] = {"cycle": "skip"}
        return _finish(summ, started, note or "lock-skip")
    try:
        md = VisionMarket()
        # ── 1) نبض المحركات ──
        try:
            md.price("BTCUSDT")
            summ["health"]["binance"] = "ok"
        except Exception as e:
            summ["health"]["binance"] = f"ERR {e}"[:120]
            summ["errors"].append(f"binance: {e}"[:150])
            return _finish(summ, started, note)
        try:
            summ["health"]["supabase"] = "ok" if db.health() else "ERR"
        except Exception as e:
            summ["health"]["supabase"] = f"ERR {e}"[:120]
            summ["errors"].append(f"supabase: {e}"[:150])
            return _finish(summ, started, note)
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

        # ── 2) أسعار → Redis ──
        try:
            for s, v in md.prices(config.ALL_SYMBOLS).items():
                cache.set(f"ct:px:{s}", v, ex=300)
        except Exception as e:
            summ["errors"].append(f"prices-cache: {e}"[:120])

        # ── 3) يوم جديد؟ ──
        today = _today()
        equity = db.realized_equity()
        if db.get_state("day", "") != today:
            db.set_state("day", today)
            db.set_state("day_start", str(equity))
            db.set_state("halted", "")
            db.log_event("INFO", f"new day {today} eq={equity:.1f}")
        day_start = float(db.get_state("day_start", str(equity)) or equity)
        # حدود الخسارة اليومية والتراجع الكلي ملغاة؛ امسح أي حالة قديمة.
        halted = None
        if db.get_state("halted", ""):
            db.set_state("halted", "")

        # ── 4) مسح + فتح (يُمنع في الإيقاف) ──
        if not halted:
            _scan_day(md, today, equity, summ, _send)
            _scan_falcon(md, today, equity, summ, _send)
        else:
            summ["errors"].append(f"halted: {halted} (الفتح متوقف)")

        # ── 5) إدارة (تعمل دائماً حتى في الإيقاف) ──
        for tr in trader.manage_all(md):
            summ["closed"] += 1
            summ["closed_day" if tr.get("system") == "DAY" else "closed_falcon"] += 1
            _send(notify.t_close(tr))

        # ── 6) رصيد + إيقاف ──
        equity = db.realized_equity()
        peak = db.mark_equity(equity)
        h = trader.check_halts(equity, day_start, peak)
        if h and not halted:
            db.set_state("halted", h)
            db.log_event("HALT", h)
            _send(notify.t_halt(h, equity))
        summ["equity"] = equity
    except Exception as e:
        summ["errors"].append(f"cycle-crash: {e}"[:200])
        try:
            db.log_event("ERR", f"cycle: {e}\n{traceback.format_exc(limit=3)}")
        except Exception:
            pass
    finally:
        cache.release("ct:cycle:lock")
    return _finish(summ, started, note)


def _finish(summ, started, note):
    ended = datetime.now(timezone.utc)
    summ["duration_ms"] = int((time.time() - started.timestamp()) * 1000)
    try:
        cycle_health = dict(summ["health"],
                           signals_day=summ.get("signals_day", 0),
                           signals_falcon=summ.get("signals_falcon", 0),
                           opened_day=summ.get("opened_day", 0),
                           opened_falcon=summ.get("opened_falcon", 0),
                           closed_day=summ.get("closed_day", 0),
                           closed_falcon=summ.get("closed_falcon", 0))
        db.save_cycle(dict(started_at=started, ended_at=ended,
                           duration_ms=summ["duration_ms"],
                           scanned_day=summ["scanned_day"],
                           scanned_falcon=summ["scanned_falcon"],
                           reject_day=summ["reject_day"], reject_falcon=summ["reject_falcon"],
                           signals=summ["signals"], opened=summ["opened"],
                           closed=summ["closed"], health=cycle_health,
                           errors=summ["errors"][:10],
                           equity=summ.get("equity", db.realized_equity()), note=note))
    except Exception:
        pass
    return summ


def _scan_day(md, today, equity, summ, send):
    p = config.DAY
    try:
        anc = _bars(md, config.ANCHOR, "15m", 130)
        a_now = st.anchor_ar_now(anc)
    except Exception as e:
        summ["errors"].append(f"DAY-anchor: {e}"[:120])
        return
    rej = Counter()
    for sym_ in config.DAY_SYMBOLS:
        summ["scanned_day"] += 1
        try:
            df = _bars(md, sym_, "15m", 300)
            h1c, h1e = st.h1_now(df)
            side, px, a, dbg = st.day_scan_debug(df, a_now, h1c, h1e, p)
            if dbg.get("reject"):
                rej[dbg["reject"]] += 1
            if side == 0:
                continue
            summ["signals"] += 1
            summ["signals_day"] += 1
            signal_key = f"DAY:{sym_}:{side}:{df.iloc[-1]['open_time'].isoformat()}"
            if [x for x in db.open_positions("DAY") if x["symbol"] == sym_]:
                rej["anti-double"] += 1
                continue
            ok, why = trader.can_open(today, "DAY", sym_)
            if not ok:
                rej[f"risk-{why}"] += 1
                continue
            sl_d = p["sl_atr"] * a
            qty = trader.position_size(equity, p["risk_per_trade"], sl_d, px)
            if qty <= 0:
                rej["risk-size"] += 1
                continue
            tr = trader.place_entry("DAY", sym_, side, qty, px,
                                    px + side * p["tp_atr"] * a, px - side * sl_d,
                                    today, p["max_hold_bars"] * 0.25,
                                    reason_ar=DAY_AR[side], signal_key=signal_key)
            if tr is None:
                rej["duplicate-signal"] += 1
                continue
            summ["opened"] += 1
            summ["opened_day"] += 1
            send(notify.t_open(tr))
        except Exception as e:
            summ["errors"].append(f"DAY-{sym_}: {e}"[:120])
    summ["reject_day"] = dict(rej)


def _scan_falcon(md, today, equity, summ, send):
    p = config.FALCON
    rej = Counter()
    for sym_ in config.FALCON_SYMBOLS:
        summ["scanned_falcon"] += 1
        try:
            df = _bars(md, sym_, "4h", 260)
            dd = _bars(md, sym_, "1d", 250)
            dc, de50, de200 = st.daily_regime(dd)
            side, px, a, dbg = st.falcon_scan_debug(df, dc, de50, de200, p)
            if dbg.get("reject"):
                rej[dbg["reject"]] += 1
            if side == 0:
                continue
            summ["signals"] += 1
            summ["signals_falcon"] += 1
            ok, why = trader.can_open(today, "FALCON", sym_)
            if not ok:
                rej[f"risk-{why}"] += 1
                continue
            if [x for x in db.open_positions("FALCON") if x["symbol"] == sym_]:
                rej["anti-double"] += 1
                continue
            signal_key = f"FALCON:{sym_}:{side}:{df.iloc[-1]['open_time'].isoformat()}"
            sl_d = p["sl_atr"] * a
            for leg, frac, tp in (("A", 0.5, px + p["tp_atr"] * a), ("B", 0.5, None)):
                qty = trader.position_size(equity, p["risk_per_trade"] * frac, sl_d, px)
                if qty <= 0:
                    rej["risk-size"] += 1
                    continue
                tr = trader.place_entry("FALCON", sym_, side, qty, px, tp or 0, px - sl_d,
                                        today, p["max_hold_bars"] * 4, leg=leg,
                                        trail_atr=p["trail_atr"] if leg == "B" else 0,
                                        atr_now=a, reason_ar=FALCON_AR, bump=(leg == "A"),
                                        signal_key=signal_key)
                if tr is None:
                    rej["duplicate-signal"] += 1
                    continue
                summ["opened"] += 1
                summ["opened_falcon"] += 1
                send(notify.t_open(tr))
        except Exception as e:
            summ["errors"].append(f"FALCON-{sym_}: {e}"[:120])
    summ["reject_falcon"] = dict(rej)
