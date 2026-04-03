CREATE TABLE IF NOT EXISTS campaign_state (
  campaign_id TEXT PRIMARY KEY,
  campaign_name TEXT NOT NULL,
  tracked_link TEXT NOT NULL,
  affiliate_source_of_truth TEXT NOT NULL DEFAULT 'seren-affiliates',
  crm_source_of_truth TEXT NOT NULL DEFAULT 'skill_owned_serendb',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS affiliate_runs (
  id SERIAL PRIMARY KEY,
  run_type TEXT NOT NULL,
  run_status TEXT NOT NULL,
  auth_path TEXT NOT NULL,
  affiliate_feed_status TEXT NOT NULL,
  provider_health TEXT NOT NULL,
  proposal_size INTEGER NOT NULL DEFAULT 10,
  new_outbound_daily_cap INTEGER NOT NULL DEFAULT 10,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS candidate_profiles (
  id SERIAL PRIMARY KEY,
  external_key TEXT NOT NULL UNIQUE,
  full_name TEXT NOT NULL,
  email TEXT,
  organization TEXT,
  source_system TEXT NOT NULL,
  source_path TEXT NOT NULL,
  relationship_hint TEXT,
  warm_score NUMERIC,
  fit_score NUMERIC,
  dnc_status TEXT NOT NULL DEFAULT 'active',
  last_seen_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS candidate_source_events (
  id SERIAL PRIMARY KEY,
  candidate_external_key TEXT NOT NULL,
  source_system TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_event_type TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  raw_payload JSONB
);

CREATE TABLE IF NOT EXISTS proposal_sets (
  id SERIAL PRIMARY KEY,
  proposal_date DATE NOT NULL,
  tracked_link TEXT NOT NULL,
  editable BOOLEAN NOT NULL DEFAULT true,
  proposal_size INTEGER NOT NULL DEFAULT 10,
  status TEXT NOT NULL DEFAULT 'draft',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS proposal_items (
  id SERIAL PRIMARY KEY,
  proposal_set_id INTEGER NOT NULL REFERENCES proposal_sets(id) ON DELETE CASCADE,
  candidate_external_key TEXT NOT NULL,
  rank_position INTEGER NOT NULL,
  candidate_score NUMERIC NOT NULL,
  rationale TEXT,
  selected BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS message_drafts (
  id SERIAL PRIMARY KEY,
  proposal_set_id INTEGER REFERENCES proposal_sets(id) ON DELETE SET NULL,
  candidate_external_key TEXT NOT NULL,
  draft_type TEXT NOT NULL,
  subject_line TEXT,
  message_body TEXT NOT NULL,
  tracked_link TEXT NOT NULL,
  approval_required BOOLEAN NOT NULL DEFAULT true,
  approval_status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS approval_events (
  id SERIAL PRIMARY KEY,
  draft_id INTEGER REFERENCES message_drafts(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  actor TEXT,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS send_batches (
  id SERIAL PRIMARY KEY,
  batch_type TEXT NOT NULL,
  proposal_set_id INTEGER REFERENCES proposal_sets(id) ON DELETE SET NULL,
  batch_status TEXT NOT NULL DEFAULT 'pending_approval',
  new_outbound_count INTEGER NOT NULL DEFAULT 0,
  reply_count INTEGER NOT NULL DEFAULT 0,
  counts_against_daily_cap BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reply_events (
  id SERIAL PRIMARY KEY,
  candidate_external_key TEXT NOT NULL,
  reply_source TEXT NOT NULL,
  classification TEXT NOT NULL,
  reply_summary TEXT,
  requires_approval BOOLEAN NOT NULL DEFAULT true,
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dnc_events (
  id SERIAL PRIMARY KEY,
  candidate_external_key TEXT NOT NULL,
  signal TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'hard_stop',
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS daily_digests (
  id SERIAL PRIMARY KEY,
  digest_date DATE NOT NULL,
  run_id INTEGER REFERENCES affiliate_runs(id) ON DELETE SET NULL,
  digest_markdown TEXT NOT NULL,
  provider_health TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
