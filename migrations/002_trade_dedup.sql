-- Prevent duplicate entries for the same strategy/symbol/signal candle.
ALTER TABLE trades ADD COLUMN IF NOT EXISTS signal_key TEXT NOT NULL DEFAULT '';

-- One DAY position per symbol while it is open.
CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_open_day_symbol
    ON trades (system, symbol)
    WHERE status = 'OPEN' AND system = 'DAY';

-- The same signal may have FALCON legs A and B, so leg is part of the identity.
CREATE UNIQUE INDEX IF NOT EXISTS uq_trades_signal_leg
    ON trades (system, symbol, signal_key, leg)
    WHERE signal_key <> '';
