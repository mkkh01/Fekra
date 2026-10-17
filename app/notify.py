"""تيليغرام: إرسال خام عبر HTTP (بدون مكتبات ثقيلة) + تنسيق الرسائل."""
from datetime import datetime
import requests
from . import config

API = "https://api.telegram.org/bot"

AR_SYS = {"DAY": "FALCON-DAY ⚡", "FALCON": "FALCON 🦅", "SNIPER": "SNIPER 🎯"}
AR_EXIT = {
    "TP": "تحقق الهدف 🎯",
    "SL": "ضرب الوقف 🛑",
    "TSL": "الوقف المتحرك 🔒",
    "BE": "نقطة التعادل (حماية) 🛡️",
    "TIME": "انتهاء الوقت ⏱️",
    "SYSTEM_RETIRED": "تصفية نظام ملغى 🧹",
}

KEYBOARD = {"inline_keyboard": [
    [{"text": "الصفقات المفتوحة", "callback_data": "open"},
     {"text": "الصفقات المغلقة", "callback_data": "closed"}],
    [{"text": "الأسعار الحالية", "callback_data": "prices"},
     {"text": "🔄 Cycle Summary", "callback_data": "cycle"}],
    [{"text": "أداء النظام", "callback_data": "perf"}],
    [{"text": "القائمة الرئيسية", "callback_data": "home"}],
]}


def _api(method, payload, timeout=15):
    if not config.BOT_TOKEN:
        return False, "no-token"
    try:
        r = requests.post(f"{API}{config.BOT_TOKEN}/{method}", json=payload, timeout=timeout)
        d = r.json()
        return bool(d.get("ok")), d
    except Exception as e:
        return False, str(e)[:200]


def send_text(chat_id, text, markup=None):
    return _api("sendMessage", {"chat_id": chat_id, "text": text,
                                "reply_markup": markup or KEYBOARD,
                                "disable_web_page_preview": True})


def edit_text(chat_id, msg_id, text, markup=None):
    return _api("editMessageText", {"chat_id": chat_id, "message_id": msg_id,
                                    "text": text, "reply_markup": markup or KEYBOARD,
                                    "disable_web_page_preview": True})


def answer_cb(cb_id, text=""):
    return _api("answerCallbackQuery", {"callback_query_id": cb_id, "text": text[:180]})


def get_me():
    ok, d = _api("getMe", {})
    return ok


def set_webhook(url, secret=""):
    """
    لا نحذف /start أو الأزرار المعلقة أثناء إعادة نشر Render.
    secret: يُرسَل كترويسة X-Telegram-Bot-Api-Secret-Token مع كل تحديث،
    وبه نتحقق أن الطلب قادم من تيليغرام فعلاً لا من أي شخص يعرف الرابط.
    """
    payload = {"url": url, "drop_pending_updates": False}
    if secret:
        payload["secret_token"] = secret
    return _api("setWebhook", payload)


def fmt_px(x):
    x = float(x)
    if x == 0:
        return "—"
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 100:
        return f"{x:,.3f}"
    if x >= 1:
        return f"{x:,.4f}"
    return f"{x:,.6f}"


def sym(s):
    return s.replace("USDT", "")


def t_open(tr):
    side_ar = "شراء 🟢" if tr["side"] == 1 else "بيع 🔴"
    leg = f" (ساق {tr['leg']})" if tr.get("leg") else ""
    px = float(tr["entry"])
    tp = float(tr.get("tp") or 0)
    sl = float(tr.get("sl") or 0)
    tp_str = fmt_px(tp)
    sl_str = fmt_px(sl)
    if px > 0:
        if tp > 0:
            tp_pct = abs(tp - px) / px * 100
            tp_str += f" (+{tp_pct:.2f}%)"
        if sl > 0:
            sl_pct = abs(px - sl) / px * 100
            sl_str += f" (-{sl_pct:.2f}%)"

    return (f"⚡ توصية جديدة — {AR_SYS.get(tr['system'], tr['system'])}{leg}\n"
            f"{sym(tr['symbol'])} {side_ar} | دخول: {fmt_px(px)}\n"
            f"🎯 هدف: {tp_str} | 🛑 وقف: {sl_str}\n"
            f"📦 كمية: {float(tr['qty']):.6f}\n"
            f"السبب: {tr.get('reason_ar') or 'إشارة فنية'}")


def t_close(tr):
    win = tr["net"] >= 0
    side_ar = "شراء 🟢" if tr["side"] == 1 else "بيع 🔴"
    leg = f" (ساق {tr['leg']})" if tr.get("leg") else ""
    reason = tr.get("reason", "")
    reason_label = AR_EXIT.get(reason, reason)
    sys_name = AR_SYS.get(tr.get("system"), tr.get("system", ""))

    px_in = float(tr.get("entry") or 0)
    px_out = float(tr.get("exit_price") or 0)
    side = int(tr.get("side") or 1)
    if px_in > 0 and px_out > 0:
        pct = (px_out - px_in) / px_in * 100 if side == 1 else (px_in - px_out) / px_in * 100
        pct_sign = "+" if pct >= 0 else ""
        pct_str = f" ({pct_sign}{pct:.2f}%)"
    else:
        pct_str = ""

    # حساب مدة الاحتفاظ بتنسيق مقروء
    held_str = ""
    entry_t = tr.get("entry_time")
    exit_t = tr.get("exit_time")
    if entry_t and exit_t:
        try:
            if isinstance(entry_t, str):
                entry_t = datetime.fromisoformat(entry_t)
            if isinstance(exit_t, str):
                exit_t = datetime.fromisoformat(exit_t)
            sec = max(0, int((exit_t - entry_t).total_seconds()))
            m, s = divmod(sec, 60)
            h, m = divmod(m, 60)
            if h > 0:
                held_str = f" | ⏱️ {h}س {m}د"
            elif m > 0:
                held_str = f" | ⏱️ {m}د {s}ث"
            else:
                held_str = f" | ⏱️ {s}ث"
        except Exception:
            pass

    return (f"{'✅ فوز' if win else '❌ خسارة'} — {sys_name} {sym(tr['symbol'])} {side_ar}{leg}\n"
            f"النتيجة: {'+' if win else ''}{tr['net']}${pct_str}{held_str}\n"
            f"السبب: {reason_label}\n"
            f"دخول {fmt_px(tr['entry'])} → خروج {fmt_px(tr['exit_price'])}")


def t_halt(reason, equity):
    ar = {"daily-loss-halt": "خسارة اليوم 3%", "max-drawdown-halt": "تراجع كلي 10%"}.get(reason, reason)
    return f"🛑 إيقاف حماية: {ar}\nالرصيد: ${equity:,.1f}\nالفتح الجديد متوقف — الإدارة مستمرة."
