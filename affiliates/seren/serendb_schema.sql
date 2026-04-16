-- seren-affiliate skill: serendb schema (database: seren_affiliate)
-- Owned by the skill. All reads and writes route through the serendb connector.
-- Published to seren-skills/affiliates/seren. Family: affiliate-v1.

-- Single-row cache of GET /affiliates/me. Upserted by sync_affiliate_profile.
-- sender_address is required before any send executes; bootstrap fails closed if empty.
CREATE TABLE IF NOT EXISTS affiliate_profile (
  agent_id TEXT PRIMARY KEY,
  referral_code TEXT NOT NULL,
  tier TEXT,
  balance_cents BIGINT NOT NULL DEFAULT 0,
  display_name TEXT,
  sender_address TEXT,
  last_synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per publisher program the user is enrolled in.
-- Refreshed from GET /affiliates/me/partner-links by sync_joined_programs.
CREATE TABLE IF NOT EXISTS joined_programs (
  program_slug TEXT PRIMARY KEY,
  program_name TEXT NOT NULL,
  program_description TEXT,
  partner_link_url TEXT NOT NULL,
  commission_summary_json JSONB,
  joined_at TIMESTAMPTZ,
  last_synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Deduped universe of contact addresses the skill has seen.
CREATE TABLE IF NOT EXISTS contacts (
  email TEXT PRIMARY KEY,
  display_name TEXT,
  source_kind TEXT NOT NULL CHECK (source_kind IN ('pasted', 'gmail_contacts', 'outlook_contacts')),
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per successful send. UNIQUE(program_slug, contact_email) enforces
-- per-program dedupe. Daily cap is computed as COUNT(*) WHERE sent_at::date = today.
CREATE TABLE IF NOT EXISTS distributions (
  distribution_id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL,
  program_slug TEXT NOT NULL,
  contact_email TEXT NOT NULL,
  provider TEXT NOT NULL CHECK (provider IN ('gmail', 'outlook')),
  subject_final TEXT NOT NULL,
  body_hash TEXT NOT NULL,
  provider_message_id TEXT,
  unsubscribe_token TEXT NOT NULL UNIQUE,
  sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT distributions_program_contact_unique UNIQUE (program_slug, contact_email)
);

CREATE INDEX IF NOT EXISTS distributions_sent_at_idx ON distributions (sent_at);
CREATE INDEX IF NOT EXISTS distributions_run_id_idx ON distributions (run_id);

-- Global opt-out list. One match blocks future sends across every program.
CREATE TABLE IF NOT EXISTS unsubscribes (
  email TEXT PRIMARY KEY,
  unsubscribed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  source TEXT NOT NULL CHECK (source IN ('link_click', 'operator_manual', 'hard_bounce'))
);

-- Per-run approved pitch. Stored so merge_and_send is idempotent and audit-traceable.
CREATE TABLE IF NOT EXISTS drafts (
  run_id TEXT PRIMARY KEY,
  program_slug TEXT NOT NULL,
  subject TEXT NOT NULL,
  body_template TEXT NOT NULL,
  model_used TEXT,
  approved_at TIMESTAMPTZ,
  approved_by TEXT
);

-- Per-affiliate, per-source watermark for incremental pulls. The link_click
-- source pulls from https://affiliates-ui.serendb.com/public/unsubscribes;
-- the row's last_synced_at is the `since` parameter on the next pull.
-- PRIMARY KEY makes watermark reads O(1) instead of scanning unsubscribes
-- for MAX(unsubscribed_at).
CREATE TABLE IF NOT EXISTS sync_state (
  agent_id TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('link_click')),
  last_synced_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (agent_id, source)
);

-- One row per skill invocation. Counts populated at persist_run_state.
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  command TEXT NOT NULL,
  program_slug TEXT,
  provider_used TEXT,
  contact_count_input INTEGER NOT NULL DEFAULT 0,
  sent_count INTEGER NOT NULL DEFAULT 0,
  skipped_dedupe INTEGER NOT NULL DEFAULT 0,
  skipped_unsub INTEGER NOT NULL DEFAULT 0,
  daily_cap_at_start INTEGER NOT NULL DEFAULT 10,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'running',
  error_text TEXT
);

CREATE INDEX IF NOT EXISTS runs_started_at_idx ON runs (started_at);
