"""السيرفر: Flask + Webhook تيليغرام + حلقة التداول الخلفية."""
import threading
import time
import traceback
from flask import Flask, request, jsonify
from . import config, db, notify, bot
from .cycle import run_cycle

app = Flask(__name__)


@app.get("/")
def index():
    try:
        eq = db.realized_equity()
        n = len(db.open_positions())
        c = db.last_cycle()
        last = f"#{c['id']} {c['duration_ms']}ms" if c else "none"
    except Exception as e:
        return f"CT starting... ({e})", 200
    return (f"<h2>🦅 CT — FALCON Paper Trading</h2>"
            f"<p>equity: ${eq:,.1f} | open: {n} | last cycle: {last}</p>"
            f"<p><a href='/health'>health</a> · <a href='/api/summary'>summary</a></p>"), 200


@app.get("/health")
def health():
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
    ok["telegram"] = "ok" if (config.BOT_TOKEN and notify.get_me()) else ("no-token" if not config.BOT_TOKEN else "ERR")
    return jsonify(ok=ok, scheduler=config.RUN_SCHEDULER,
                   interval=config.SCAN_INTERVAL_SEC)


@app.get("/api/summary")
def api_summary():
    try:
        c = db.last_cycle()
        return jsonify(ok=True, equity=db.realized_equity(),
                       open=len(db.open_positions()),
                       stats_day=db.get_stats("DAY"),
                       stats_falcon=db.get_stats("FALCON"),
                       last_cycle=c, halted=db.get_state("halted", ""))
    except Exception as e:
        return jsonify(ok=False, error=str(e)[:200]), 500


@app.post("/webhook/telegram")
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    try:
        # Telegram ينتظر ردًا سريعًا؛ لا نربط استجابة webhook باتصالات DB/API.
        threading.Thread(target=bot.handle_update, args=(update,), daemon=True).start()
        msg = update.get("message", {}) if isinstance(update, dict) else {}
        print(f"[telegram-webhook] update={update.get('update_id')} text={msg.get('text', '')!r}", flush=True)
    except Exception as e:
        try:
            db.log_event("ERR", f"webhook: {e}")
        except Exception:
            pass
    return jsonify(ok=True)


def _scheduler():
    """حلقة دائمة: لا يموت الخيط أبداً — كل خطأ يُسجل (في القاعدة أو في السجل)."""
    while True:
        t0 = time.time()
        try:
            s = run_cycle()
            print(f"[cycle] day={s.get('scanned_day', 0)} falcon={s.get('scanned_falcon', 0)} "
                  f"sig={s.get('signals', 0)} open={s.get('opened', 0)} close={s.get('closed', 0)} "
                  f"{s.get('duration_ms', 0)}ms errs={len(s.get('errors', []))}", flush=True)
        except Exception as e:
            msg = f"scheduler: {e}\n{traceback.format_exc(limit=3)}"
            print(f"[cycle-crash] {msg}", flush=True)  # يظهر في سجلات Render دائماً
            try:
                db.log_event("ERR", msg[:2000])
            except Exception as e2:
                print(f"[cycle-db-log-failed] {e2}", flush=True)
        # فترة ثابتة: دورة كل 60ث بالضبط من بداية لبداية (لا تدحرج)
        time.sleep(max(5, config.SCAN_INTERVAL_SEC - (time.time() - t0)))


def boot():
    # ── 0) مخطط القاعدة: طبّق أي مايجريشن ناقص قبل أي شيء (يُصلح قاعدة حية قديمة) ──
    try:
        errs = db.ensure_schema()
        print(f"[schema] {'FAILED: ' + '; '.join(errs) if errs else 'ok'}", flush=True)
    except Exception as e:
        print(f"[schema] ERR {e}", flush=True)
    if config.PUBLIC_URL and config.BOT_TOKEN:
        try:
            ok, d = notify.set_webhook(f"{config.PUBLIC_URL}/webhook/telegram")
            print(f"webhook: {'OK' if ok else d}", flush=True)
        except Exception as e:
            print(f"webhook ERR: {e}", flush=True)
    if config.RUN_SCHEDULER:
        threading.Thread(target=_scheduler, daemon=True).start()
        print("scheduler: ON", flush=True)
    else:
        print("scheduler: OFF", flush=True)


boot()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(__import__("os").environ.get("PORT", "5000")))
