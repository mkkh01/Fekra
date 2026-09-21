"""تيليغرام: إرسال خام عبر HTTP (بدون مكتبات ثقيلة) + تنسيق الرسائل."""
import requests
from . import config

API = "https://api.telegram.org/bot"

AR_SYS = {"DAY": "FALCON-DAY ⚡", "FALCON": "FALCON 🦅"}
AR_EXIT = {"TP": "تحقق الهدف 🎯", "SL": "ضرب الوقف 🛑", "TSL": "الوقف المتحرك 🔒", "TIME": "انتهاء الوقت ⏱️"}

KEYBOARD = {"inline_keyboard": [
    [{"text": "الصفقات المفتوحة", "callback_data": "open"},
     {"text": "الصفقات المغلقة", "callback_data": "closed"}],
    [{"text": "الأسعار الحالية", "callback_data": "prices"},
     {"text": "🔄 Samurai Cycle", "callback_data": "cycle"}],
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


def set_webhook(url):
    # لا نحذف /start أو الأزرار المعلقة أثناء إعادة نشر Render.
    return _api("setWebhook", {"url": url, "drop_pending_updates": False})


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
    return (f"⚡ <b>توصية جديدة — {AR_SYS.get(tr['system'], tr['system'])}{leg}</b>\n"
            f"{sym(tr['symbol'])} {side_ar} | دخول: {fmt_px(tr['entry'])}\n"
            f"🎯 هدف: {fmt_px(tr['tp'])} | 🛑 وقف: {fmt_px(tr['sl'])}\n"
            f"📦 كمية: {float(tr['qty']):.6f}\n"
            f"السبب: {tr.get('reason_ar') or 'إشارة فنية'}").replace("<b>", "").replace("</b>", "")


def t_close(tr):
    win = tr["net"] >= 0
    side_ar = "شراء" if tr["side"] == 1 else "بيع"
    leg = f" (ساق {tr['leg']})" if tr.get("leg") else ""
    return (f"{'✅ فوز' if win else '❌ خسارة'} — {sym(tr['symbol'])} {side_ar}{leg}\n"
            f"النتيجة: {'+' if win else ''}{tr['net']}$ (R={tr['r']})\n"
            f"السبب: {AR_EXIT.get(tr['reason'], tr['reason'])}\n"
            f"دخول {fmt_px(tr['entry'])} → خروج {fmt_px(tr['exit_price'])}")


def t_halt(reason, equity):
    ar = {"daily-loss-halt": "خسارة اليوم 3%", "max-drawdown-halt": "تراجع كلي 10%"}.get(reason, reason)
    return f"🛑 إيقاف حماية: {ar}\nالرصيد: ${equity:,.1f}\nالفتح الجديد متوقف — الإدارة مستمرة."
