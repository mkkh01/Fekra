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
# هل يُسمح بتسجيل أول من يرسل /start كمدير؟ (خطر استيلاء — افتراضياً لا)
ALLOW_AUTO_ADMIN = _env("ALLOW_AUTO_ADMIN", "0") == "1"

SCAN_INTERVAL_SEC = _ienv("SCAN_INTERVAL_SEC", 60)
PAPER_EQUITY = _fenv("PAPER_EQUITY", 10000)
RUN_SCHEDULER = _env("RUN_SCHEDULER", "1") == "1"

# ── حلقة الإدارة السريعة (منفصلة عن حلقة المسح) ──
# المسح يبقى كل SCAN_INTERVAL_SEC، لكن تقييم TP/SL يحتاج دقة أعلى
# حتى لا يُكتشف كسر الوقف بعد فوات الأوان.
MONITOR_INTERVAL_SEC = _ienv("MONITOR_INTERVAL_SEC", 5)
# مهلة الشبكة لكل محاولة (كانت 20 ثانية × 10 نقاط = حتى 200 ثانية للنداء الواحد!)
MARKET_TIMEOUT_SEC = _fenv("MARKET_TIMEOUT_SEC", 6.0)

# ── المحركات المعطّلة (لإيقاف نظام دون حذف كوده) ──
ENABLE_DAY = _env("ENABLE_DAY", "1") == "1"
ENABLE_FALCON = _env("ENABLE_FALCON", "1") == "1"

# ── العوالم ──
DAY_SYMBOLS = ["SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT", "NEARUSDT",
               "DOTUSDT", "UNIUSDT", "ETCUSDT", "FILUSDT", "ICPUSDT", "VETUSDT", "ALGOUSDT"]
ANCHOR = "BTCUSDT"
FALCON_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "LINKUSDT"]
ALL_SYMBOLS = sorted(set(DAY_SYMBOLS + FALCON_SYMBOLS + [ANCHOR]))

# ── FALCON-DAY params (مجمّدة — لا تُغيَّر إلا بعد إعادة اختبار) ──
DAY = dict(dev=1.2, btc_cap=0.6, rsiX=40, adx_max=35, atr_min_pct=0.0004,
           atr_max_pct=0.02, vol_mult=0.8, tp_atr=1.0, sl_atr=2.0,
           max_hold_bars=24, max_per_day=3, risk_per_trade=0.005)

# ── FALCON 4H params (مجمّدة) ──
# أضفنا max_per_day بعد أن كان الرقم 3 مكتوباً يدوياً داخل trader.py
FALCON = dict(don=20, vol_mult=1.5, adx_min=18, mom_mult=0.8, sl_atr=3.0,
              tp_atr=3.0, trail_atr=4.0, max_hold_bars=150, cooldown=5,
              risk_per_trade=0.01, max_per_day=3)

# ── المخاطر ──
RISK = dict(max_concurrent=6,
            daily_loss_halt=_fenv("DAILY_LOSS_HALT", 0.0),      # 0 = معطّل
            max_drawdown_halt=_fenv("MAX_DRAWDOWN_HALT", 0.0),  # 0 = معطّل
            max_notional_pct=0.95, min_notional_usd=10.0,
            fee_side=0.0002, slip_side=0.0002)

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
