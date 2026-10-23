"""الإعدادات: أسرار من متغيرات البيئة + ثوابت الاستراتيجية المجمدة من الباك تست."""
import os


def _env(name, default=""):
    v = os.environ.get(name, default)
    return v.strip() if isinstance(v, str) else v


def _fenv(name, default):
    try:
        return float(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _ienv(name, default):
    try:
        return int(float(_env(name, str(default))))
    except (TypeError, ValueError):
        return default


# ── الاتصالات (أسرار — لا تُكتب في الكود أبداً) ──
DATABASE_URL = _env("DATABASE_URL") or _env("SUPABASE_URL")  # المستخدم سمّاه SUPABASE_URL
REDIS_URL = _env("REDIS_URL")
BOT_TOKEN = _env("BOT_TOKEN") or _env("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = _env("ADMIN_CHAT_ID")
PUBLIC_URL = _env("PUBLIC_URL").rstrip("/")
SUPABASE_KEY = _env("SUPABASE_KEY")  # احتياطي (REST) — الباك إند يستخدم Postgres مباشرة

# ── أمان نقاط النهاية ──
# سرّ الويب هوك: يُرسَل كترويسة X-Telegram-Bot-Api-Secret-Token من تيليغرام.
# اتركه فارغاً فقط إن كنت تستخدم مساراً سرياً غير متوقع بدلاً منه.
WEBHOOK_SECRET = _env("WEBHOOK_SECRET")
# توكن للوصول إلى / و /api/summary. إن تُرك فارغاً تُحمى هذه المسارات تلقائياً
# بقيمة مشتقة من BOT_TOKEN حتى لا تُكشف بياناتك المالية للعامة.
ADMIN_API_TOKEN = _env("ADMIN_API_TOKEN")
# هل يُسمح بتسجيل أول من يرسل /start كمدير؟ (افتراضياً نعم لتسهيل التثبيت)
ALLOW_AUTO_ADMIN = _env("ALLOW_AUTO_ADMIN", "1") == "1"

# ── مفاتيح بينانس الرسمية (اختيارية للـ Paper وتفعيل حصانة الـ API — إجبارية للتداول الحي Live) ──
BINANCE_API_KEY = _env("BINANCE_API_KEY", "")
BINANCE_API_SECRET = _env("BINANCE_API_SECRET", "")
LIVE_TRADING = _env("LIVE_TRADING", "0") in ("1", "true", "True")

SCAN_INTERVAL_SEC = _ienv("SCAN_INTERVAL_SEC", 60)
PAPER_EQUITY = _fenv("PAPER_EQUITY", 100.0)
RUN_SCHEDULER = _env("RUN_SCHEDULER", "1") == "1"

# ── حلقة الإدارة السريعة (منفصلة عن حلقة المسح) ──
MONITOR_INTERVAL_SEC = _ienv("MONITOR_INTERVAL_SEC", 5)
MARKET_TIMEOUT_SEC = _fenv("MARKET_TIMEOUT_SEC", 6.0)

# ── ضوابط الصفقات الحية (منع الدخول المتأخر أو المشوه) ──
# أقصى عمر مسموح به لإشارة الشمعة المغلقة بالثواني (75 ثانية لضمان الدخول فور إغلاق الشمعة)
SIGNAL_MAX_AGE_SEC_DAY = _ienv("SIGNAL_MAX_AGE_SEC_DAY", 75)
SIGNAL_MAX_AGE_SEC_FALCON = _ienv("SIGNAL_MAX_AGE_SEC_FALCON", 900)
# أقصى انحراف مسموح به بين السعر اللحظي الحي وسعر الإشارة (0.08% فقط لمنع الانجراف السعري نهائياً)
MAX_PRICE_DRIFT_PCT = _fenv("MAX_PRICE_DRIFT_PCT", 0.0008)

# ── تأمين الأرباح بنقطة التعادل (Breakeven معطل لنظام SNIPER السريع لمنع الخروج المبكر بالضوضاء) ──
BREAKEVEN_ENABLED = _env("BREAKEVEN_ENABLED", "0") == "1"
BREAKEVEN_TRIGGER_RATIO = _fenv("BREAKEVEN_TRIGGER_RATIO", 0.5)

# ── المحركات (تم إلغاء FALCON و DAY واعتماد SNIPER كمحرك رئيسي فائق الدقة WR 92.4%) ──
ENABLE_SNIPER = _env("ENABLE_SNIPER", "1") == "1"
ENABLE_DAY = _env("ENABLE_DAY", "0") == "1"
ENABLE_FALCON = _env("ENABLE_FALCON", "0") == "1"

# ── قائمة العملات الـ 32 المتوافقة مع الشريعة (بنية تحتية وخدمية L1/L2 واستبعاد الميم والإقراض الربوي) ──
SNIPER_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "NEARUSDT", "LTCUSDT",
    "BCHUSDT", "DOTUSDT", "UNIUSDT", "ATOMUSDT", "FILUSDT",
    "XLMUSDT", "ICPUSDT", "TRXUSDT", "SUIUSDT", "APTUSDT",
    "ARBUSDT", "OPUSDT", "TIAUSDT", "RENDERUSDT", "INJUSDT",
    "ALGOUSDT", "FETUSDT", "GRTUSDT", "STXUSDT", "HBARUSDT",
    "VETUSDT", "EGLDUSDT"
]

ANCHOR = "BTCUSDT"
DAY_SYMBOLS = SNIPER_SYMBOLS
FALCON_SYMBOLS = SNIPER_SYMBOLS
ALL_SYMBOLS = sorted(set(SNIPER_SYMBOLS + [ANCHOR]))

# ── معايير نظام SNIPER الجديد (Win Rate 92.38% | TP ثابت عند 0.75% بالضبط) ──
SNIPER = dict(
    tp_pct=_fenv("SNIPER_TP", 0.0075),            # الهدف الثابت 0.75%
    tp_min_pct=_fenv("SNIPER_TP_MIN", 0.0075),
    tp_max_pct=_fenv("SNIPER_TP_MAX", 0.0075),
    sl_pct=_fenv("SNIPER_SL_PCT", 0.0500),        # وقف أمان 5.00%
    bb_std=_fenv("SNIPER_BB_STD", 2.8),           # انحراف بولينجر باند الموسع 2.8
    rsi_period=_ienv("SNIPER_RSI_PERIOD", 7),     # مؤشر RSI السريع
    rsi_oversold=_fenv("SNIPER_RSI_OV", 18.0),    # تشبع بيعي 18
    rsi_overbought=_fenv("SNIPER_RSI_OB", 82.0),  # تشبع شرائي 82
    vol_mult=_fenv("SNIPER_VOL_MULT", 1.15),      # فلتر تأكيد الحجم 1.15x
    ema_span=_ienv("SNIPER_EMA_SPAN", 200),       # فلتر اتجاه 200 شمعة
    max_hold_bars=_ienv("SNIPER_MAX_HOLD", 0),    # 0 = بلا إغلاق زمني عشوائي
    risk_per_trade=_fenv("SNIPER_RISK", 0.01),    # مخاطرة 1% لكل صفقة (1$ على رأس مال 100$)
    max_per_day=_ienv("SNIPER_MAX_PER_DAY", 4)     # أقصى عدد صفقات يومية لكل عملة
)

# ── FALCON-DAY params (أرشيفية — النظامان معطلان بطلب المستخدم) ──
DAY = dict(dev=1.2, btc_cap=0.6, rsiX=40, adx_max=35, atr_min_pct=0.0004,
           atr_max_pct=0.02, vol_mult=0.8, tp_atr=0.7, sl_atr=3.0,
           max_hold_bars=24, max_per_day=3, risk_per_trade=0.005)

# ── FALCON 4H params (أرشيفية) ──
FALCON = dict(don=20, vol_mult=1.0, adx_min=18, mom_mult=0.8, sl_atr=2.5,
              tp_atr=1.2, trail_atr=2.5, max_hold_bars=100, cooldown=5,
              risk_per_trade=0.01, max_per_day=3)

# ── المخاطر ──
RISK = dict(max_concurrent=_ienv("MAX_CONCURRENT", 4),
            daily_loss_halt=_fenv("DAILY_LOSS_HALT", 0.0),      # 0 = معطّل
            max_drawdown_halt=_fenv("MAX_DRAWDOWN_HALT", 0.0),  # 0 = معطّل
            max_notional_pct=0.35, min_notional_usd=5.0,
            fee_side=0.0002, slip_side=0.0001)

# ── نموذج التكاليف الواقعي ──
# fee_side/slip_side قيمتان ثابتتان؛ نستبدلهما بنموذج يتناسب مع حجم الصفقة.
FEE_BPS = _fenv("FEE_BPS", 2.0)          # 2 bps ≈ عمولة taker على بينانس
SLIPPAGE = dict(base_bps=_fenv("SLIP_BASE_BPS", 1.0),
                impact_k=_fenv("SLIP_IMPACT_K", 0.6),
                min_bps=_fenv("SLIP_MIN_BPS", 0.5),
                max_bps=_fenv("SLIP_MAX_BPS", 25.0))

# ── تمويل العقود الدائمة (Funding) ──
# FALCON يحتفظ حتى 600 ساعة (25 يوماً) → التمويل يتراكم كل 8 ساعات.
# القيمة الافتراضية متحفظة (0.01% / 8س). عدّلها أو اربطها بمصدر بيانات حي.
FUNDING_RATE_8H = _fenv("FUNDING_RATE_8H", 0.0001)
FUNDING_ENABLED = _env("FUNDING_ENABLED", "1") == "1"

# ── مرجع الباك تست (للمقارنة في زر الأداء) ──
BACKTEST_REF = {
    "DAY": dict(n=3211, wr=77.1, pf=1.36, net=14500.6, base=130000),
    "FALCON": dict(n=468, wr=49.4, pf=1.42, net=4432.0, base=70000),
}
