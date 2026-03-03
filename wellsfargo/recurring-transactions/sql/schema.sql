CREATE TABLE IF NOT EXISTS wf_recurring_runs (
  run_id TEXT PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  patterns_found INTEGER NOT NULL DEFAULT 0,
  total_monthly_committed NUMERIC(14,2) NOT NULL DEFAULT 0,
  txn_count INTEGER NOT NULL DEFAULT 0,
  artifact_root TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wf_recurring_patterns (
  id SERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES wf_recurring_runs(run_id) ON DELETE CASCADE,
  payee_normalized TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'uncategorized',
  frequency TEXT NOT NULL,
  avg_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
  median_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
  occurrence_count INTEGER NOT NULL DEFAULT 0,
  confidence NUMERIC(5,4) NOT NULL DEFAULT 0,
  first_seen DATE NOT NULL,
  last_seen DATE NOT NULL,
  next_expected DATE,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (run_id, payee_normalized, frequency)
);

CREATE INDEX IF NOT EXISTS idx_wf_recurring_patterns_run ON wf_recurring_patterns(run_id);
CREATE INDEX IF NOT EXISTS idx_wf_recurring_patterns_payee ON wf_recurring_patterns(payee_normalized);

CREATE TABLE IF NOT EXISTS wf_recurring_snapshots (
  run_id TEXT PRIMARY KEY REFERENCES wf_recurring_runs(run_id) ON DELETE CASCADE,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  patterns_found INTEGER NOT NULL DEFAULT 0,
  total_monthly_committed NUMERIC(14,2) NOT NULL DEFAULT 0,
  patterns_json JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wf_recurring_snapshots_period ON wf_recurring_snapshots(period_start, period_end);

CREATE OR REPLACE VIEW v_wf_recurring_latest AS
SELECT s.*
FROM wf_recurring_snapshots s
JOIN wf_recurring_runs r ON r.run_id = s.run_id
WHERE r.status = 'success'
AND r.ended_at = (
  SELECT MAX(r2.ended_at)
  FROM wf_recurring_runs r2
  WHERE r2.status = 'success'
);

CREATE OR REPLACE VIEW v_wf_recurring_active AS
SELECT p.*
FROM wf_recurring_patterns p
JOIN wf_recurring_runs r ON r.run_id = p.run_id
WHERE r.status = 'success'
  AND p.is_active = TRUE
AND r.ended_at = (
  SELECT MAX(r2.ended_at)
  FROM wf_recurring_runs r2
  WHERE r2.status = 'success'
)
ORDER BY p.avg_amount DESC;
