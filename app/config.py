"""الإعدادات: أسرار من متغيرات البيئة + ثوابت الاستراتيجية المجمدة من الباك تست."""
import os


def _env(name, default=""):
    v = os.environ.get(name, default)
    return v.strip() if isinstance(v, str) else v


# ── الاتصالات (أسرار — لا تُكتب في الكود أبداً) ──
DATABASE_URL = _env("DATABASE_URL") or _env("SUPABASE_URL")  # المستخدم سمّاه SUPABASE_URL
REDIS_URL = _env("REDIS_URL")
BOT_TOKEN = _env("BOT_TOKEN") or _env("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = _env("ADMIN_CHAT_ID")
PUBLIC_URL = _env("PUBLIC_URL").rstrip("/")
SUPABASE_KEY = _env("SUPABASE_KEY")  # احتياطي (REST) — الباك إند يستخدم Postgres مباشرة

SCAN_INTERVAL_SEC = int(_env("SCAN_INTERVAL_SEC", "60") or 60)
PAPER_EQUITY = float(_env("PAPER_EQUITY", "10000") or 10000)
RUN_SCHEDULER = _env("RUN_SCHEDULER", "1") == "1"

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
FALCON = dict(don=20, vol_mult=1.5, adx_min=18, mom_mult=0.8, sl_atr=3.0,
              tp_atr=3.0, trail_atr=4.0, max_hold_bars=150, cooldown=5,
              risk_per_trade=0.01)

# ── المخاطر ──
RISK = dict(max_concurrent=6, daily_loss_halt=0.0, max_drawdown_halt=0.0,
            max_notional_pct=0.95, min_notional_usd=10.0,
            fee_side=0.0002, slip_side=0.0002)

# ── مرجع الباك تست (للمقارنة في زر الأداء) ──
BACKTEST_REF = {
    "DAY": dict(n=3211, wr=77.1, pf=1.36, net=14500.6, base=130000),
    "FALCON": dict(n=468, wr=49.4, pf=1.42, net=4432.0, base=70000),
}
