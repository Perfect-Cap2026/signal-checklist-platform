-- init.sql: Creates signals table
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument TEXT NOT NULL,
    sector TEXT,
    timeframe TEXT,
    signal_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB,
    htf_result TEXT,
    key_pass BOOLEAN,
    pattern_pass BOOLEAN,
    confirm_pass BOOLEAN,
    news_pass BOOLEAN,
    overall_pass BOOLEAN,
    reason JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_signals_time ON signals(signal_time);
CREATE INDEX IF NOT EXISTS idx_signals_instrument ON signals(instrument);
