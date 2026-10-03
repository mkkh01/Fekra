-- جودة التنفيذ: أعمدة التكاليف، الوقف المتحرك المحفوظ، ومنع تكرار مراكز FALCON.
-- آمن للتنفيذ المتكرر (IF NOT EXISTS).

-- ── تكاليف التنفيذ الفعلية (للتدقيق والمقارنة مع الباك تست) ──
ALTER TABLE trades ADD COLUMN IF NOT EXISTS funding       DOUBLE PRECISION DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS spread_bps   DOUBLE PRECISION;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS slip_bps     DOUBLE PRECISION;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_ref    DOUBLE PRECISION;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS client_order_id TEXT;

-- الوقف المتحرك الحالي (كان يُحسب في الذاكرة ويُفقد بين الدورات)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS sl_current   DOUBLE PRECISION;

-- ── فهارس الأداء ──
CREATE INDEX IF NOT EXISTS idx_equity_marks_equity ON equity_marks(equity DESC);
CREATE INDEX IF NOT EXISTS idx_events_ts           ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_trades_system_status ON trades(system, status);

-- ── منع تكرار المراكز المفتوحة لـ FALCON على مستوى القاعدة ──
-- نظام DAY كان محمياً بفهرس فريد؛ FALCON كان يعتمد على فحص في بايثون فقط
-- (عرضة لتسابق الدورات). الساقان A و B مسموحان معاً، لذا leg جزء من الهوية.
CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_open_falcon_leg
    ON trades (system, symbol, leg)
    WHERE status = 'OPEN' AND system = 'FALCON';
