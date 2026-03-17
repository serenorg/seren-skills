CREATE SCHEMA IF NOT EXISTS {{schema_name}};

CREATE TABLE IF NOT EXISTS {{schema_name}}.sessions (
  session_id TEXT PRIMARY KEY,
  command TEXT NOT NULL,
  severity_threshold TEXT NOT NULL,
  lookback_days INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.runs (
  run_id TEXT PRIMARY KEY,
  session_id TEXT REFERENCES {{schema_name}}.sessions(session_id),
  status TEXT NOT NULL,
  findings_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.events (
  event_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES {{schema_name}}.runs(run_id),
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.audit_findings (
  finding_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES {{schema_name}}.runs(run_id),
  severity TEXT NOT NULL,
  title TEXT NOT NULL,
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.loss_hypotheses (
  hypothesis_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES {{schema_name}}.runs(run_id),
  impact_level TEXT NOT NULL,
  hypothesis TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.artifacts (
  artifact_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES {{schema_name}}.runs(run_id),
  artifact_type TEXT NOT NULL,
  artifact_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
