-- CT schema (Supabase Postgres)
CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    system TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side INT NOT NULL,
    leg TEXT DEFAULT '',
    entry_time TIMESTAMPTZ DEFAULT now(),
    entry DOUBLE PRECISION NOT NULL,
    qty DOUBLE PRECISION NOT NULL,
    tp DOUBLE PRECISION DEFAULT 0,
    sl DOUBLE PRECISION DEFAULT 0,
    trail_atr DOUBLE PRECISION DEFAULT 0,
    atr0 DOUBLE PRECISION DEFAULT 0,
    hold_hours DOUBLE PRECISION DEFAULT 24,
    status TEXT DEFAULT 'OPEN',
    exit_time TIMESTAMPTZ,
    exit_price DOUBLE PRECISION,
    reason TEXT,
    net DOUBLE PRECISION,
    r DOUBLE PRECISION,
    fee DOUBLE PRECISION,
    day DATE,
    reason_ar TEXT DEFAULT '',
    signal_key TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_day ON trades(day);

CREATE TABLE IF NOT EXISTS day_counts (
    day DATE NOT NULL,
    system TEXT NOT NULL,
    symbol TEXT NOT NULL,
    n INT DEFAULT 0,
    PRIMARY KEY (day, system, symbol)
);

CREATE TABLE IF NOT EXISTS equity_marks (
    ts TIMESTAMPTZ DEFAULT now(),
    equity DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS ct_cycles (
    id SERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    duration_ms INT NOT NULL,
    scanned_day INT DEFAULT 0,
    scanned_falcon INT DEFAULT 0,
    reject_day JSONB DEFAULT '{}',
    reject_falcon JSONB DEFAULT '{}',
    signals INT DEFAULT 0,
    opened INT DEFAULT 0,
    closed INT DEFAULT 0,
    health JSONB DEFAULT '{}',
    errors JSONB DEFAULT '[]',
    equity DOUBLE PRECISION,
    note TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS events (
    id SERIAL PRIMARY KEY,
    ts TIMESTAMPTZ DEFAULT now(),
    level TEXT NOT NULL,
    msg TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
