CREATE TABLE IF NOT EXISTS wf_networth_runs (
  run_id TEXT PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  starting_balance NUMERIC(14,2) NOT NULL DEFAULT 0,
  ending_balance NUMERIC(14,2) NOT NULL DEFAULT 0,
  total_inflows NUMERIC(14,2) NOT NULL DEFAULT 0,
  total_outflows NUMERIC(14,2) NOT NULL DEFAULT 0,
  net_change NUMERIC(14,2) NOT NULL DEFAULT 0,
  months_count INTEGER NOT NULL DEFAULT 0,
  txn_count INTEGER NOT NULL DEFAULT 0,
  artifact_root TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wf_networth_monthly (
  id SERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES wf_networth_runs(run_id) ON DELETE CASCADE,
  month_start DATE NOT NULL,
  inflows NUMERIC(14,2) NOT NULL DEFAULT 0,
  outflows NUMERIC(14,2) NOT NULL DEFAULT 0,
  net NUMERIC(14,2) NOT NULL DEFAULT 0,
  running_balance NUMERIC(14,2) NOT NULL DEFAULT 0,
  txn_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (run_id, month_start)
);

CREATE INDEX IF NOT EXISTS idx_wf_networth_monthly_run ON wf_networth_monthly(run_id);

CREATE TABLE IF NOT EXISTS wf_networth_snapshots (
  run_id TEXT PRIMARY KEY REFERENCES wf_networth_runs(run_id) ON DELETE CASCADE,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  starting_balance NUMERIC(14,2) NOT NULL DEFAULT 0,
  ending_balance NUMERIC(14,2) NOT NULL DEFAULT 0,
  net_change NUMERIC(14,2) NOT NULL DEFAULT 0,
  monthly_json JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE VIEW v_wf_networth_latest AS
SELECT s.* FROM wf_networth_snapshots s
JOIN wf_networth_runs r ON r.run_id = s.run_id
WHERE r.status = 'success'
AND r.ended_at = (SELECT MAX(r2.ended_at) FROM wf_networth_runs r2 WHERE r2.status = 'success');

CREATE OR REPLACE VIEW v_wf_networth_trend AS
SELECT m.* FROM wf_networth_monthly m
JOIN wf_networth_runs r ON r.run_id = m.run_id
WHERE r.status = 'success'
AND r.ended_at = (SELECT MAX(r2.ended_at) FROM wf_networth_runs r2 WHERE r2.status = 'success')
ORDER BY m.month_start;
