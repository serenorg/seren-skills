-- SaaS Short Strategy Bot persistence schema for SerenDB
-- Supports paper, paper-sim, and live tracking with unified PnL reporting.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS trading;

CREATE TABLE IF NOT EXISTS trading.strategy_runs (
  run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  strategy_name TEXT NOT NULL DEFAULT 'saas-short-trader',
  mode TEXT NOT NULL CHECK (mode IN ('paper', 'paper-sim', 'live')),
  run_date DATE NOT NULL DEFAULT CURRENT_DATE,
  status TEXT NOT NULL DEFAULT 'completed',
  universe TEXT[] NOT NULL,
  max_names_scored INTEGER,
  max_names_orders INTEGER,
  min_conviction NUMERIC(6, 2),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trading.candidate_scores (
  id BIGSERIAL PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
  ticker TEXT NOT NULL,
  rank_no INTEGER,
  selected BOOLEAN NOT NULL DEFAULT FALSE,
  f NUMERIC(4, 2) NOT NULL,
  a NUMERIC(4, 2) NOT NULL,
  s NUMERIC(4, 2) NOT NULL,
  t NUMERIC(4, 2) NOT NULL,
  p NUMERIC(4, 2) NOT NULL,
  conviction_0_100 NUMERIC(6, 2) NOT NULL,
  latest_filing_date DATE,
  latest_filing_type TEXT,
  evidence_sec JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_news JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_trends JSONB NOT NULL DEFAULT '{}'::jsonb,
  catalyst_type TEXT,
  catalyst_date DATE,
  catalyst_bias TEXT,
  catalyst_confidence TEXT CHECK (catalyst_confidence IN ('LOW', 'MED', 'HIGH') OR catalyst_confidence IS NULL),
  catalyst_note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (run_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_candidate_scores_run_id ON trading.candidate_scores(run_id);
CREATE INDEX IF NOT EXISTS idx_candidate_scores_ticker ON trading.candidate_scores(ticker);

CREATE TABLE IF NOT EXISTS trading.order_events (
  id BIGSERIAL PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
  mode TEXT NOT NULL CHECK (mode IN ('paper', 'paper-sim', 'live')),
  order_ref TEXT NOT NULL,
  broker TEXT NOT NULL DEFAULT 'alpaca',
  ticker TEXT NOT NULL,
  side TEXT NOT NULL,
  order_type TEXT NOT NULL,
  status TEXT NOT NULL,
  qty NUMERIC(18, 6) NOT NULL,
  limit_price NUMERIC(18, 6),
  stop_price NUMERIC(18, 6),
  filled_qty NUMERIC(18, 6),
  filled_avg_price NUMERIC(18, 6),
  is_simulated BOOLEAN NOT NULL DEFAULT FALSE,
  event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (run_id, order_ref, event_time)
);

CREATE INDEX IF NOT EXISTS idx_order_events_run_id ON trading.order_events(run_id);
CREATE INDEX IF NOT EXISTS idx_order_events_ticker_mode_time ON trading.order_events(ticker, mode, event_time DESC);

CREATE TABLE IF NOT EXISTS trading.position_marks_daily (
  as_of_date DATE NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('paper', 'paper-sim', 'live')),
  ticker TEXT NOT NULL,
  qty NUMERIC(18, 6) NOT NULL,
  avg_entry_price NUMERIC(18, 6) NOT NULL,
  mark_price NUMERIC(18, 6) NOT NULL,
  market_value NUMERIC(18, 6) NOT NULL,
  realized_pnl NUMERIC(18, 6) NOT NULL DEFAULT 0,
  unrealized_pnl NUMERIC(18, 6) NOT NULL DEFAULT 0,
  gross_exposure NUMERIC(18, 6),
  net_exposure NUMERIC(18, 6),
  source_run_id UUID REFERENCES trading.strategy_runs(run_id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (as_of_date, mode, ticker)
);

CREATE INDEX IF NOT EXISTS idx_position_marks_mode_date ON trading.position_marks_daily(mode, as_of_date DESC);

CREATE TABLE IF NOT EXISTS trading.pnl_daily (
  as_of_date DATE NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('paper', 'paper-sim', 'live')),
  realized_pnl NUMERIC(18, 6) NOT NULL DEFAULT 0,
  unrealized_pnl NUMERIC(18, 6) NOT NULL DEFAULT 0,
  net_pnl NUMERIC(18, 6) NOT NULL DEFAULT 0,
  gross_exposure NUMERIC(18, 6),
  net_exposure NUMERIC(18, 6),
  hit_rate NUMERIC(8, 4),
  max_drawdown NUMERIC(18, 6),
  source_run_id UUID REFERENCES trading.strategy_runs(run_id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (as_of_date, mode)
);

CREATE INDEX IF NOT EXISTS idx_pnl_daily_mode_date ON trading.pnl_daily(mode, as_of_date DESC);

CREATE OR REPLACE VIEW trading.v_pnl_summary AS
WITH base AS (
  SELECT
    mode,
    as_of_date,
    realized_pnl,
    unrealized_pnl,
    net_pnl
  FROM trading.pnl_daily
),
mtd AS (
  SELECT
    mode,
    COALESCE(SUM(realized_pnl), 0) AS mtd_realized_pnl,
    COALESCE(SUM(unrealized_pnl), 0) AS mtd_unrealized_pnl,
    COALESCE(SUM(net_pnl), 0) AS mtd_net_pnl
  FROM base
  WHERE as_of_date >= date_trunc('month', CURRENT_DATE)::date
  GROUP BY mode
),
itd AS (
  SELECT
    mode,
    COALESCE(SUM(realized_pnl), 0) AS itd_realized_pnl,
    COALESCE(SUM(unrealized_pnl), 0) AS itd_unrealized_pnl,
    COALESCE(SUM(net_pnl), 0) AS itd_net_pnl
  FROM base
  GROUP BY mode
),
latest AS (
  SELECT DISTINCT ON (mode)
    mode,
    as_of_date AS latest_date
  FROM base
  ORDER BY mode, as_of_date DESC
)
SELECT
  l.mode,
  l.latest_date,
  COALESCE(m.mtd_realized_pnl, 0) AS mtd_realized_pnl,
  COALESCE(m.mtd_unrealized_pnl, 0) AS mtd_unrealized_pnl,
  COALESCE(m.mtd_net_pnl, 0) AS mtd_net_pnl,
  COALESCE(i.itd_realized_pnl, 0) AS itd_realized_pnl,
  COALESCE(i.itd_unrealized_pnl, 0) AS itd_unrealized_pnl,
  COALESCE(i.itd_net_pnl, 0) AS itd_net_pnl
FROM latest l
LEFT JOIN mtd m ON m.mode = l.mode
LEFT JOIN itd i ON i.mode = l.mode;
