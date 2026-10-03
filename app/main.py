"""
السيرفر: Flask + ويب هوك تيليغرام + حلقة المسح + حلقة المراقبة السريعة.

الإصلاحات الأمنية:
  * الويب هوك يتحقق من ترويسة X-Telegram-Bot-Api-Secret-Token (كان مفتوحاً للجميع).
  * / و /api/summary محميتان بتوكن (كانتا تكشفان الرصيد والصفقات للعامة).
  * /health يعيد حالة مبسّطة بدون بيانات مالية.
"""
import hashlib
import hmac
import os
import threading
import time
import traceback

from flask import Flask, request, jsonify, abort

from . import config, db, notify, bot
from .cycle import run_cycle, monitor_once

app = Flask(__name__)


# ────────────────────────────── الحماية ──────────────────────────────
def _api_token():
    """توكن الوصول للواجهة؛ إن لم يُضبط يُشتقّ من BOT_TOKEN حتى لا تبقى مكشوفة."""
    return config.ADMIN_API_TOKEN or (
        hashlib.sha256(f"ct-falcon:{config.BOT_TOKEN}".encode()).hexdigest()[:32]
        if config.BOT_TOKEN else "")


def _authorized():
    tok = _api_token()
    if not tok:
        return False                       # بلا إعدادات → لا نكشف شيئاً
    got = request.headers.get("Authorization", "")
    if got.startswith("Bearer "):
        got = got[7:]
    got = got or request.args.get("token", "")
    return hmac.compare_digest(got, tok)


def _guard():
    if not _authorized():
        abort(401)


# ────────────────────────────── المسارات ──────────────────────────────
@app.get("/")
def index():
    _guard()
    try:
        c = db.last_cycle()
        pos = db.open_positions()
        last = f"#{c['id']} {c['duration_ms']}ms" if c else "none"
        eq = db.equity()
        n = len(pos)
    except Exception as e:
        return f"CT starting... ({e})", 200
    return (f"<h2>🦅 CT — FALCON Paper Trading</h2>"
            f"<p>equity: ${eq:,.1f} | open: {n} | last cycle: {last}</p>"
            f"<p><a href='/health'>health</a> · <a href='/api/summary'>summary</a></p>"), 200


@app.get("/health")
def health():
    """فحص صحة مبسّط — بلا بيانات مالية (يصلح كـ healthCheckPath)."""
    ok = {"binance": "?", "supabase": "?", "redis": "?", "telegram": "?", "schema": "?"}
    try:
        from .market import VisionMarket
        VisionMarket().price("BTCUSDT")
        ok["binance"] = "ok"
    except Exception as e:
        ok["binance"] = str(e)[:100]
    try:
        ok["supabase"] = "ok" if db.health() else "ERR"
    except Exception as e:
        ok["supabase"] = str(e)[:100]
    try:
        ok["schema"] = "ok" if db.schema_ok() else "missing-columns"
    except Exception as e:
        ok["schema"] = str(e)[:100]
    try:
        from .cache import cache
        ok["redis"] = "ok" if cache.ping() else "ERR"
    except Exception as e:
        ok["redis"] = str(e)[:100]
    ok["telegram"] = "ok" if (config.BOT_TOKEN and notify.get_me()) else \
        ("no-token" if not config.BOT_TOKEN else "ERR")
    return jsonify(ok=ok, scheduler=config.RUN_SCHEDULER,
                   interval=config.SCAN_INTERVAL_SEC,
                   monitor=config.MONITOR_INTERVAL_SEC,
                   engines={"DAY": config.ENABLE_DAY, "FALCON": config.ENABLE_FALCON})


@app.get("/api/summary")
def api_summary():
    _guard()
    try:
        pos = db.open_positions()
        return jsonify(ok=True, equity=db.equity(),
                       realized=db.realized_equity(),
                       open=len(pos),
                       stats_day=db.get_stats("DAY"),
                       stats_falcon=db.get_stats("FALCON"),
                       last_cycle=db.last_cycle(),
                       halted=db.get_state("halted", ""))
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:200]), 500


@app.post("/webhook/telegram")
def webhook():
    # التحقق من هوية المُرسِل: تيليغرام يوقّع كل تحديث بهذه الترويسة
    secret = config.WEBHOOK_SECRET
    if secret:
        if not hmac.compare_digest(
                request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), secret):
            abort(401)
    update = request.get_json(force=True, silent=True) or {}
    try:
        threading.Thread(target=bot.handle_update, args=(update,), daemon=True).start()
        msg = update.get("message", {}) if isinstance(update, dict) else {}
        print(f"[telegram-webhook] update={update.get('update_id')} "
              f"text={msg.get('text', '')!r}", flush=True)
    except Exception as e:
        try:
            db.log_event("ERR", f"webhook: {e}")
        except Exception:
            pass
    return jsonify(ok=True)


# ────────────────────────────── الحلقات الخلفية ──────────────────────────────
def _scheduler():
    while True:
        t0 = time.monotonic()
        try:
            s = run_cycle()
            print(f"[cycle] day={s.get('scanned_day', 0)} falcon={s.get('scanned_falcon', 0)} "
                  f"sig={s.get('signals', 0)} open={s.get('opened', 0)} "
                  f"close={s.get('closed', 0)} {s.get('duration_ms', 0)}ms "
                  f"errs={len(s.get('errors', []))}", flush=True)
        except Exception as e:
            msg = f"scheduler: {e}\n{traceback.format_exc(limit=3)}"
            print(f"[cycle-crash] {msg}", flush=True)
            try:
                db.log_event("ERR", msg[:2000])
            except Exception as e2:
                print(f"[cycle-db-log-failed] {e2}", flush=True)
        time.sleep(max(5, config.SCAN_INTERVAL_SEC - (time.monotonic() - t0)))


def _monitor_loop():
    """تقييم TP/SL كل بضع ثوانٍ بدل انتظار إغلاق شمعة 15 دقيقة."""
    while True:
        t0 = time.monotonic()
        try:
            for tr in monitor_once():
                adm = config.ADMIN_CHAT_ID or db.get_state("admin_chat_id", "")
                if adm and config.BOT_TOKEN:
                    notify.send_text(adm, notify.t_close(tr))
        except Exception as e:
            try:
                db.log_event("ERR", f"monitor-loop: {e}")
            except Exception:
                pass
        time.sleep(max(1, config.MONITOR_INTERVAL_SEC - (time.monotonic() - t0)))


_booted = False


def boot():
    global _booted
    if _booted:
        return
    _booted = True
    try:
        errs = db.ensure_schema()
        print(f"[schema] {'FAILED: ' + '; '.join(errs) if errs else 'ok'}", flush=True)
    except Exception as e:
        print(f"[schema] ERR {e}", flush=True)
    if config.PUBLIC_URL and config.BOT_TOKEN:
        try:
            ok, d = notify.set_webhook(f"{config.PUBLIC_URL}/webhook/telegram",
                                       config.WEBHOOK_SECRET)
            print(f"webhook: {'OK' if ok else d}", flush=True)
        except Exception as e:
            print(f"webhook ERR: {e}", flush=True)
    if not config.ADMIN_CHAT_ID and not config.ALLOW_AUTO_ADMIN:
        print("[warn] ADMIN_CHAT_ID غير مضبوط وتسجيل المدير التلقائي معطّل — "
              "لن يستجيب البوت لأحد.", flush=True)
    if config.RUN_SCHEDULER:
        threading.Thread(target=_scheduler, daemon=True).start()
        print("scheduler: ON", flush=True)
    else:
        print("scheduler: OFF", flush=True)
    threading.Thread(target=_monitor_loop, daemon=True).start()
    print(f"monitor: ON ({config.MONITOR_INTERVAL_SEC}s)", flush=True)


boot()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
