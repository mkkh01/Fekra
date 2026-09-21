-- ═══════════════════════════════════════════════════════════════════════════
--  CT — FALCON Paper Trading 🦅
--  ملف بناء قاعدة البيانات الكامل (Supabase Postgres)
-- ═══════════════════════════════════════════════════════════════════════════
--  كيفية الاستخدام:
--    Supabase → SQL Editor → الصق الملف كاملاً → Run.
--
--  ✓ آمن للتنفيذ عدة مرات (كل العبارات IF NOT EXISTS / ON CONFLICT).
--  ✓ يبني النظام كاملاً من الصفر على قاعدة فارغة.
--  ✓ يعمل أيضاً كـ"إصلاح شامل" على قاعدة حية قديمة: يضيف أي أعمدة/فهارس
--    ناقصة دون المساس بالبيانات الموجودة (يحل خطأ column "signal_key"
--    of relation "trades" does not exist نهائياً).
--  ✓ يسجّل المايجريشنز (001، 002) كمطبَّقة في جدول state ليطابق متتبّع
--    التطبيق (schema_migrations) الواقع الفعلي.
-- ═══════════════════════════════════════════════════════════════════════════


-- ─────────────────────────────────────────────
-- 1) trades — الصفقات (مفتوحة ومغلقة)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    id           SERIAL PRIMARY KEY,
    system       TEXT NOT NULL,                 -- 'DAY' أو 'FALCON'
    symbol       TEXT NOT NULL,                 -- مثل ETHUSDT
    side         INT  NOT NULL,                 -- 1 = لونج، -1 = شورت
    leg          TEXT DEFAULT '',               -- FALCON: 'A' أو 'B'
    entry_time   TIMESTAMPTZ DEFAULT now(),
    entry        DOUBLE PRECISION NOT NULL,
    qty          DOUBLE PRECISION NOT NULL,
    tp           DOUBLE PRECISION DEFAULT 0,
    sl           DOUBLE PRECISION DEFAULT 0,
    trail_atr    DOUBLE PRECISION DEFAULT 0,    -- مضاعف ATR للوقف المتحرك (ساق B)
    atr0         DOUBLE PRECISION DEFAULT 0,    -- ATR لحظة الدخول
    hold_hours   DOUBLE PRECISION DEFAULT 24,   -- أقصى مدة احتفاظ (خروج زمني)
    status       TEXT DEFAULT 'OPEN',           -- 'OPEN' أو 'CLOSED'
    exit_time    TIMESTAMPTZ,
    exit_price   DOUBLE PRECISION,
    reason       TEXT,                          -- سبب الخروج: TP/SL/TSL/TIME
    net          DOUBLE PRECISION,              -- صافي الربح/الخسارة بالدولار
    r            DOUBLE PRECISION,              -- النتيجة بوحدة R
    fee          DOUBLE PRECISION,              -- العمولات + الانزلاق
    day          DATE,                          -- يوم التداول (لحدود اليوم)
    reason_ar    TEXT DEFAULT '',               -- سبب الدخول بالعربية (للإشعارات)
    signal_key   TEXT NOT NULL DEFAULT ''       -- بصمة الإشارة (منع التكرار)
);

-- أعمدة/فهارس احتياطية: تُضيف الناقص فقط إذا كانت القاعدة قديمة (لا تلمس الموجود)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS leg        TEXT DEFAULT '';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS tp         DOUBLE PRECISION DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS sl         DOUBLE PRECISION DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS trail_atr  DOUBLE PRECISION DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS atr0       DOUBLE PRECISION DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS hold_hours DOUBLE PRECISION DEFAULT 24;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS status     TEXT DEFAULT 'OPEN';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_time  TIMESTAMPTZ;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_price DOUBLE PRECISION;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS reason     TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS net        DOUBLE PRECISION;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS r          DOUBLE PRECISION;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS fee        DOUBLE PRECISION;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS day        DATE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS reason_ar  TEXT DEFAULT '';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_key TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_trades_status  ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_day     ON trades(day);
CREATE INDEX IF NOT EXISTS idx_trades_open    ON trades(system, symbol) WHERE status = 'OPEN';
CREATE INDEX IF NOT EXISTS idx_trades_closed  ON trades(id DESC) WHERE status = 'CLOSED';

-- ── قيود منع التكرار (dedup) ──
-- مركز DAY واحد مفتوح لكل عملة.
CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_open_day_symbol
    ON trades (system, symbol)
    WHERE status = 'OPEN' AND system = 'DAY';

-- نفس الإشارة لا تُفتح مرتين (قد تتكرر FALCON بساقيها A وB لذا leg جزء من الهوية).
-- ⚠️ سيفشل الإنشاء فقط إذا كانت لديك بيانات قديمة مكررة نفس الإشارة — بقية
--    الملف يُطبَّق رغم ذلك (كل عبارة مستقلة).
CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_signal_leg
    ON trades (system, symbol, signal_key, leg)
    WHERE signal_key <> '';


-- ─────────────────────────────────────────────
-- 2) day_counts — عدّاد صفقات اليوم (حد المخاطر اليومية)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS day_counts (
    day    DATE NOT NULL,
    system TEXT NOT NULL,
    symbol TEXT NOT NULL,
    n      INT DEFAULT 0,
    PRIMARY KEY (day, system, symbol)
);


-- ─────────────────────────────────────────────
-- 3) equity_marks — نبض الرصيد (لحساب القمة والتراجع)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS equity_marks (
    ts     TIMESTAMPTZ DEFAULT now(),
    equity DOUBLE PRECISION NOT NULL
);


-- ─────────────────────────────────────────────
-- 4) ct_cycles — سجل الدورات (كل دورة مسح/تداول كل 60 ثانية)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ct_cycles (
    id             SERIAL PRIMARY KEY,
    started_at     TIMESTAMPTZ NOT NULL,
    ended_at       TIMESTAMPTZ NOT NULL,
    duration_ms    INT NOT NULL,
    scanned_day    INT DEFAULT 0,          -- عملات DAY المفحوصة
    scanned_falcon INT DEFAULT 0,          -- عملات FALCON المفحوصة
    reject_day     JSONB DEFAULT '{}',     -- أسباب رفض DAY (تجميع)
    reject_falcon  JSONB DEFAULT '{}',     -- أسباب رفض FALCON
    signals        INT DEFAULT 0,
    opened         INT DEFAULT 0,
    closed         INT DEFAULT 0,
    health         JSONB DEFAULT '{}',     -- نبض المحركات (binance/redis/...)
    errors         JSONB DEFAULT '[]',     -- أخطاء الدورة
    equity         DOUBLE PRECISION,
    note           TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_ct_cycles_started ON ct_cycles(started_at DESC);


-- ─────────────────────────────────────────────
-- 5) events — سجل الأحداث والأخطاء (فتح/إغلاق/خطأ/إيقاف)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id   SERIAL PRIMARY KEY,
    ts   TIMESTAMPTZ DEFAULT now(),
    level TEXT NOT NULL,                     -- INFO / ORDER / EXIT / ERR / HALT
    msg  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_level ON events(level, id DESC);


-- ─────────────────────────────────────────────
-- 6) state — حالة عامة (اليوم الحالي، رصيد بداية اليوم، الإيقاف، المايجريشنز...)
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);


-- ─────────────────────────────────────────────
-- 7) تسجيل المايجريشنز كمطبَّقة (يتطابق مع متتبّع التطبيق ensure_schema)
-- ─────────────────────────────────────────────
INSERT INTO state (key, value)
VALUES ('schema_migrations', '["001_schema.sql", "002_trade_dedup.sql"]')
ON CONFLICT (key) DO NOTHING;


-- ═══════════════════════════════════════════════════════════════════════════
--  تحقق — سيظهر أسفل النتائج بعد التشغيل
-- ═══════════════════════════════════════════════════════════════════════════
-- أعمدة trades (يجب أن تتضمن: leg, tp, sl, trail_atr, atr0, hold_hours,
-- day, reason_ar, signal_key ...)
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'trades'
ORDER BY ordinal_position;

-- الفهارس (يجب أن تتضمن: uq_trades_signal_leg, uq_trades_open_day_symbol)
SELECT indexname FROM pg_indexes WHERE tablename = 'trades';

-- الجداول المنشأة
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
ORDER BY table_name;

-- متتبّع المايجريشنز
SELECT * FROM state WHERE key = 'schema_migrations';
