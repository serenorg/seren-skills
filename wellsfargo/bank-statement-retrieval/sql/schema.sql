CREATE TABLE IF NOT EXISTS wf_runs (
  run_id TEXT PRIMARY KEY,
  started_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  mode TEXT NOT NULL,
  error_code TEXT,
  selector_profile_version TEXT,
  artifact_root TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wf_statement_files (
  file_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES wf_runs(run_id) ON DELETE CASCADE,
  account_masked TEXT NOT NULL,
  statement_period_start DATE,
  statement_period_end DATE,
  local_file_path TEXT NOT NULL,
  sha256 CHAR(64) NOT NULL,
  bytes BIGINT NOT NULL,
  download_status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wf_statement_files_run_id ON wf_statement_files(run_id);
CREATE INDEX IF NOT EXISTS idx_wf_statement_files_account ON wf_statement_files(account_masked);

CREATE TABLE IF NOT EXISTS wf_transactions (
  row_hash TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES wf_runs(run_id) ON DELETE CASCADE,
  file_id TEXT REFERENCES wf_statement_files(file_id) ON DELETE SET NULL,
  account_masked TEXT NOT NULL,
  txn_date DATE,
  post_date DATE,
  description_raw TEXT NOT NULL,
  amount NUMERIC(14,2) NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  statement_period_start DATE,
  statement_period_end DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wf_transactions_run_id ON wf_transactions(run_id);
CREATE INDEX IF NOT EXISTS idx_wf_transactions_account ON wf_transactions(account_masked);
CREATE INDEX IF NOT EXISTS idx_wf_transactions_txn_date ON wf_transactions(txn_date);

CREATE TABLE IF NOT EXISTS wf_txn_categories (
  row_hash TEXT PRIMARY KEY REFERENCES wf_transactions(row_hash) ON DELETE CASCADE,
  category_source TEXT NOT NULL,
  category TEXT NOT NULL,
  confidence NUMERIC(5,4),
  rationale_short TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wf_monthly_summary (
  run_id TEXT NOT NULL REFERENCES wf_runs(run_id) ON DELETE CASCADE,
  account_masked TEXT NOT NULL,
  month_start DATE NOT NULL,
  debit_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  credit_total NUMERIC(14,2) NOT NULL DEFAULT 0,
  txn_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, account_masked, month_start)
);

CREATE INDEX IF NOT EXISTS idx_wf_monthly_summary_month ON wf_monthly_summary(month_start);
