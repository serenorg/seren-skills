-- Self-learning extension schema for saas-short-trader
-- Apply after serendb_schema.sql.

CREATE SCHEMA IF NOT EXISTS trading;

CREATE TABLE IF NOT EXISTS trading.learning_feature_snapshots (
  id BIGSERIAL PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
  mode TEXT NOT NULL CHECK (mode IN ('paper', 'paper-sim', 'live')),
  run_type TEXT,
  ticker TEXT NOT NULL,
  as_of_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  policy_version TEXT NOT NULL DEFAULT 'v1.0.0',
  feature_vector JSONB NOT NULL DEFAULT '{}'::jsonb,
  decision JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (run_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_learning_feature_run_id
  ON trading.learning_feature_snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_learning_feature_ticker_time
  ON trading.learning_feature_snapshots(ticker, as_of_ts DESC);

CREATE TABLE IF NOT EXISTS trading.learning_outcome_labels (
  id BIGSERIAL PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
  ticker TEXT NOT NULL,
  horizon TEXT NOT NULL CHECK (horizon IN ('5D', '10D', '20D')),
  label_date DATE NOT NULL DEFAULT CURRENT_DATE,
  realized_return NUMERIC(12, 6),
  realized_pnl NUMERIC(18, 6),
  beat_hurdle BOOLEAN,
  stop_hit BOOLEAN,
  target_hit BOOLEAN,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (run_id, ticker, horizon)
);

CREATE INDEX IF NOT EXISTS idx_learning_labels_date
  ON trading.learning_outcome_labels(label_date DESC);

CREATE TABLE IF NOT EXISTS trading.learning_policy_versions (
  policy_version TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  status TEXT NOT NULL CHECK (status IN ('champion', 'challenger', 'shadow', 'retired')),
  objective TEXT NOT NULL DEFAULT 'maximize_risk_adjusted_pnl',
  weights JSONB NOT NULL,
  thresholds JSONB NOT NULL DEFAULT '{}'::jsonb,
  training_window_start DATE,
  training_window_end DATE,
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  promoted_at TIMESTAMPTZ,
  retired_at TIMESTAMPTZ,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS trading.learning_policy_assignments (
  id BIGSERIAL PRIMARY KEY,
  run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
  policy_version TEXT NOT NULL REFERENCES trading.learning_policy_versions(policy_version),
  assignment_type TEXT NOT NULL CHECK (assignment_type IN ('champion', 'challenger', 'shadow')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (run_id, assignment_type)
);

CREATE INDEX IF NOT EXISTS idx_learning_assignment_run_id
  ON trading.learning_policy_assignments(run_id);

CREATE TABLE IF NOT EXISTS trading.learning_events (
  id BIGSERIAL PRIMARY KEY,
  event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  event_type TEXT NOT NULL CHECK (event_type IN ('retrain', 'promote', 'rollback', 'drift_alert', 'data_gap')),
  status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'blocked', 'failed')),
  policy_version TEXT,
  details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_learning_events_time
  ON trading.learning_events(event_time DESC);

CREATE OR REPLACE VIEW trading.v_learning_policy_health AS
WITH champion AS (
  SELECT
    policy_version,
    created_at,
    promoted_at,
    metrics
  FROM trading.learning_policy_versions
  WHERE status = 'champion'
  ORDER BY COALESCE(promoted_at, created_at) DESC
  LIMIT 1
),
latest_event AS (
  SELECT
    event_time,
    event_type,
    status,
    policy_version
  FROM trading.learning_events
  ORDER BY event_time DESC
  LIMIT 1
)
SELECT
  c.policy_version AS champion_policy_version,
  c.created_at AS champion_created_at,
  c.promoted_at AS champion_promoted_at,
  c.metrics AS champion_metrics,
  e.event_time AS latest_learning_event_time,
  e.event_type AS latest_learning_event_type,
  e.status AS latest_learning_event_status,
  e.policy_version AS latest_learning_event_policy
FROM champion c
LEFT JOIN latest_event e ON TRUE;
