CREATE SCHEMA IF NOT EXISTS {{schema_name}};

CREATE TABLE IF NOT EXISTS {{schema_name}}.sessions (
  session_id TEXT PRIMARY KEY,
  command TEXT NOT NULL,
  recent_window_days INTEGER NOT NULL,
  weekly_target_markets INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.runs (
  run_id TEXT PRIMARY KEY,
  session_id TEXT REFERENCES {{schema_name}}.sessions(session_id),
  status TEXT NOT NULL,
  reminder_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.events (
  event_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES {{schema_name}}.runs(run_id),
  event_type TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.engagement_events (
  engagement_event_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES {{schema_name}}.runs(run_id),
  engagement_type TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.checkin_recommendations (
  recommendation_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES {{schema_name}}.runs(run_id),
  recommendation TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.artifacts (
  artifact_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES {{schema_name}}.runs(run_id),
  artifact_type TEXT NOT NULL,
  artifact_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
