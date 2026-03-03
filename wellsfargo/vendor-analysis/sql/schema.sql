CREATE TABLE IF NOT EXISTS wf_vendor_runs (
  run_id TEXT PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  unique_vendors INTEGER NOT NULL DEFAULT 0,
  total_spend NUMERIC(14,2) NOT NULL DEFAULT 0,
  txn_count INTEGER NOT NULL DEFAULT 0,
  artifact_root TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wf_vendor_merchants (
  id SERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES wf_vendor_runs(run_id) ON DELETE CASCADE,
  vendor_normalized TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'uncategorized',
  total_spend NUMERIC(14,2) NOT NULL DEFAULT 0,
  txn_count INTEGER NOT NULL DEFAULT 0,
  avg_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
  first_seen DATE NOT NULL,
  last_seen DATE NOT NULL,
  spend_rank INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (run_id, vendor_normalized)
);

CREATE INDEX IF NOT EXISTS idx_wf_vendor_merchants_run ON wf_vendor_merchants(run_id);

CREATE TABLE IF NOT EXISTS wf_vendor_snapshots (
  run_id TEXT PRIMARY KEY REFERENCES wf_vendor_runs(run_id) ON DELETE CASCADE,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  unique_vendors INTEGER NOT NULL DEFAULT 0,
  total_spend NUMERIC(14,2) NOT NULL DEFAULT 0,
  top_vendors_json JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE VIEW v_wf_vendor_latest AS
SELECT s.* FROM wf_vendor_snapshots s
JOIN wf_vendor_runs r ON r.run_id = s.run_id
WHERE r.status = 'success'
AND r.ended_at = (SELECT MAX(r2.ended_at) FROM wf_vendor_runs r2 WHERE r2.status = 'success');

CREATE OR REPLACE VIEW v_wf_vendor_top_merchants AS
SELECT m.* FROM wf_vendor_merchants m
JOIN wf_vendor_runs r ON r.run_id = m.run_id
WHERE r.status = 'success'
AND r.ended_at = (SELECT MAX(r2.ended_at) FROM wf_vendor_runs r2 WHERE r2.status = 'success')
ORDER BY m.total_spend DESC;
