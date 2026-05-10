-- Phase 11 — skill-owned SerenDB schema (plan §17.1).
--
-- Templated on `{{schema_name}}` so the runtime can target a per-deployment
-- schema (e.g. `prophet_bounty_runner` for prod, `prophet_bounty_runner_dev`
-- for the implementer's QA box). All tables are CREATE IF NOT EXISTS so
-- re-applying on a populated database is a no-op.
--
-- Three load-bearing invariants enforced here, not just in code:
--
--   1. `markets_created.prophet_market_id` is PRIMARY KEY. The operator's
--      reconciler uses this as the per-market idempotency key (plan §18.6,
--      ADR P1). A duplicate insert is the canonical "this market is
--      already on the ledger" signal — re-inserting the same id is the
--      idempotency-fail path, not a "let's just persist twice" silent dup.
--
--   2. `markets_created.resolves_at < 2026-05-11T00:00:00Z` CHECK. A
--      defense-in-depth gate after the per-row eligibility check in
--      `agent._cmd_run`. If application logic ever stops enforcing the
--      deadline (refactor, schema drift, copy-paste error), the database
--      refuses the insert and routes the row to events as ineligible —
--      better than silently submitting an out-of-window market that
--      Prophet later rejects and that the operator pays no bounty for.
--
--   3. `runs.status` constrained to the §17.2 enum. Adding a new status
--      requires a deliberate schema change rather than a runtime typo
--      slipping through ("blocked_OTP" vs "blocked_otp" would otherwise
--      be a silent persistence bug).

CREATE SCHEMA IF NOT EXISTS {{schema_name}};

CREATE TABLE IF NOT EXISTS {{schema_name}}.runs (
  run_id        TEXT PRIMARY KEY,
  bounty_id     TEXT NOT NULL,
  user_id       TEXT NOT NULL,
  command       TEXT NOT NULL CHECK (command IN ('setup', 'run', 'status')),
  dry_run       BOOLEAN NOT NULL,
  status        TEXT NOT NULL CHECK (status IN (
    'succeeded',
    'dry_run',
    'blocked_auth',
    'blocked_otp',
    'blocked_publisher',
    'blocked_no_bounty',
    'blocked_bounty_spec_mismatch',
    'blocked_dedup_unavailable',
    'blocked_identity_mismatch',
    'blocked_identity_drift',
    'partial_failure',
    'failed'
  )),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.participant_identity (
  bounty_id          TEXT NOT NULL,
  seren_user_id      TEXT NOT NULL,
  prophet_viewer_id  TEXT NOT NULL,
  prophet_email      TEXT NOT NULL,
  captured_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (bounty_id, seren_user_id)
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.markets_created (
  prophet_market_id     TEXT PRIMARY KEY,
  run_id                TEXT NOT NULL REFERENCES {{schema_name}}.runs(run_id),
  prophet_market_url    TEXT NOT NULL,
  polymarket_source_url TEXT NOT NULL,
  resolves_at           TIMESTAMPTZ NOT NULL CHECK (resolves_at < TIMESTAMPTZ '2026-05-11T00:00:00Z'),
  prophet_viewer_id     TEXT NOT NULL,
  bounty_id             TEXT NOT NULL,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS markets_created_viewer_idx
  ON {{schema_name}}.markets_created (bounty_id, prophet_viewer_id);

CREATE TABLE IF NOT EXISTS {{schema_name}}.submissions (
  submission_id  TEXT PRIMARY KEY,
  bounty_id      TEXT NOT NULL,
  run_id         TEXT NOT NULL REFERENCES {{schema_name}}.runs(run_id),
  status         TEXT NOT NULL,
  payload        JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {{schema_name}}.events (
  event_id   BIGSERIAL PRIMARY KEY,
  run_id     TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
