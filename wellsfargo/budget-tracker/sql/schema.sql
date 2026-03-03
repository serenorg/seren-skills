CREATE TABLE IF NOT EXISTS wf_budget_runs (
  run_id TEXT PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  total_budget NUMERIC(14,2) NOT NULL DEFAULT 0,
  total_actual NUMERIC(14,2) NOT NULL DEFAULT 0,
  total_variance NUMERIC(14,2) NOT NULL DEFAULT 0,
  categories_over INTEGER NOT NULL DEFAULT 0,
  txn_count INTEGER NOT NULL DEFAULT 0,
  artifact_root TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wf_budget_categories (
  id SERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES wf_budget_runs(run_id) ON DELETE CASCADE,
  category TEXT NOT NULL,
  label TEXT NOT NULL,
  budget_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
  actual_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
  variance NUMERIC(14,2) NOT NULL DEFAULT 0,
  utilization_pct NUMERIC(7,2) NOT NULL DEFAULT 0,
  txn_count INTEGER NOT NULL DEFAULT 0,
  is_over_budget BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (run_id, category)
);

CREATE INDEX IF NOT EXISTS idx_wf_budget_categories_run ON wf_budget_categories(run_id);

CREATE TABLE IF NOT EXISTS wf_budget_snapshots (
  run_id TEXT PRIMARY KEY REFERENCES wf_budget_runs(run_id) ON DELETE CASCADE,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  total_budget NUMERIC(14,2) NOT NULL DEFAULT 0,
  total_actual NUMERIC(14,2) NOT NULL DEFAULT 0,
  total_variance NUMERIC(14,2) NOT NULL DEFAULT 0,
  categories_json JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wf_budget_snapshots_period ON wf_budget_snapshots(period_start, period_end);

CREATE OR REPLACE VIEW v_wf_budget_latest AS
SELECT s.*
FROM wf_budget_snapshots s
JOIN wf_budget_runs r ON r.run_id = s.run_id
WHERE r.status = 'success'
AND r.ended_at = (
  SELECT MAX(r2.ended_at)
  FROM wf_budget_runs r2
  WHERE r2.status = 'success'
);

CREATE OR REPLACE VIEW v_wf_budget_over_limit AS
SELECT c.*
FROM wf_budget_categories c
JOIN wf_budget_runs r ON r.run_id = c.run_id
WHERE r.status = 'success'
  AND c.is_over_budget = TRUE
AND r.ended_at = (
  SELECT MAX(r2.ended_at)
  FROM wf_budget_runs r2
  WHERE r2.status = 'success'
)
ORDER BY c.variance;
