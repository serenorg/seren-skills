-- prophet-polymarket-edge v1 schema (design doc §10.2).
-- Idempotent DDL applied on every invoke before any read or write.

CREATE SCHEMA IF NOT EXISTS {{schema_name}};

-- Wallet identity. v1 stores only a salted hash of the user's pasted input
-- plus a redacted display form (§10.4 / §13.19). `email` is NOT a valid
-- `resolved_from` value because email entry is not supported in v1 (§7.2).
CREATE TABLE IF NOT EXISTS {{schema_name}}.wallet_identities (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    organization_id TEXT,
    polymarket_proxy_wallet TEXT NOT NULL,
    resolved_from TEXT NOT NULL CHECK (resolved_from IN ('url','direct_paste')),
    source_input_hash TEXT NOT NULL,
    source_input_redacted TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, polymarket_proxy_wallet)
);

-- One row per audit/run invocation. v1 does not run Surface A loss audits,
-- but Surface B/C runs persist a row here so recommendations and telemetry
-- can foreign-key consistently with the post-v1 Surface A path.
CREATE TABLE IF NOT EXISTS {{schema_name}}.audit_runs (
    id SERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    wallet_identity_id INTEGER REFERENCES {{schema_name}}.wallet_identities(id),
    run_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_completed_at TIMESTAMPTZ,
    trade_window_start DATE,
    trade_window_end DATE,
    trades_pulled INTEGER,
    trades_matched_to_consensus INTEGER,
    match_coverage_pct_by_volume NUMERIC,
    realized_pnl_usd NUMERIC,
    premium_paid_usd NUMERIC,
    premium_recovered_usd NUMERIC,
    premium_lost_usd NUMERIC,
    surfaces_invoked TEXT[],
    status TEXT CHECK (status IN ('running','completed','failed','blocked','disclosure_declined')),
    failure_reason TEXT
);

-- Surface A pattern findings. Empty in v1 (Surface A is post-v1) but the
-- table exists so the schema is a single source of truth.
CREATE TABLE IF NOT EXISTS {{schema_name}}.audit_findings (
    id SERIAL PRIMARY KEY,
    audit_run_id INTEGER REFERENCES {{schema_name}}.audit_runs(id),
    pattern_id TEXT NOT NULL,
    polymarket_market_id TEXT,
    polymarket_canonical_id TEXT,
    entry_ts TIMESTAMPTZ,
    exit_ts TIMESTAMPTZ,
    position_size_usd NUMERIC,
    entry_price NUMERIC,
    exit_price NUMERIC,
    consensus_probability_at_entry NUMERIC,
    divergence_at_entry_bps INTEGER,
    realized_pnl_usd NUMERIC,
    premium_paid_usd NUMERIC,
    premium_recovered_usd NUMERIC,
    premium_lost_usd NUMERIC,
    consensus_unavailable BOOLEAN DEFAULT FALSE,
    confidence TEXT CHECK (confidence IN ('low','medium','high')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Surface B + Surface C output rows.
CREATE TABLE IF NOT EXISTS {{schema_name}}.recommendations (
    id SERIAL PRIMARY KEY,
    audit_run_id INTEGER REFERENCES {{schema_name}}.audit_runs(id),
    surface TEXT CHECK (surface IN ('B_tranche1','C_polymarket')),
    rank INTEGER,
    source TEXT CHECK (source IN ('prophet_create','prophet_existing','polymarket_existing')),
    market_description TEXT,
    market_url TEXT,
    suggested_side TEXT CHECK (suggested_side IN ('long','short','none')),
    consensus_probability NUMERIC,
    current_market_price NUMERIC,
    divergence_bps INTEGER,
    rationale TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- §13.4 paid-recommendation disclosure ledger. Retained for 3 years
-- minimum (legal traceability). `--purge` preserves this table.
CREATE TABLE IF NOT EXISTS {{schema_name}}.disclosure_acknowledgements (
    id SERIAL PRIMARY KEY,
    user_id_hash TEXT NOT NULL,
    disclosure_version TEXT NOT NULL,
    acknowledgement_text_hash TEXT NOT NULL,
    channel_surface TEXT NOT NULL,
    acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id_hash, acknowledgement_text_hash)
);

-- §13.19 cost-estimate gate. v1 ships the table for forward-compatibility
-- with Surface A; Surface B/C runs do not write rows here.
CREATE TABLE IF NOT EXISTS {{schema_name}}.cost_estimate_gates (
    id SERIAL PRIMARY KEY,
    audit_run_id INTEGER REFERENCES {{schema_name}}.audit_runs(id),
    estimate_basis TEXT NOT NULL CHECK (estimate_basis IN ('pre_wallet_worst_case','post_wallet_refined')),
    estimated_serenbucks NUMERIC,
    trade_window_days INTEGER,
    trades_in_estimate INTEGER,
    user_response TEXT CHECK (user_response IN ('accepted','declined','default_top50','full_history')),
    responded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- §13.18 Surface B benefit disclosure. The renderer refuses to emit
-- watchlist deep links unless a row exists for the audit run.
CREATE TABLE IF NOT EXISTS {{schema_name}}.surface_b_benefit_disclosures (
    id SERIAL PRIMARY KEY,
    audit_run_id INTEGER REFERENCES {{schema_name}}.audit_runs(id),
    disclosure_text_hash TEXT NOT NULL,
    disclosed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (audit_run_id, disclosure_text_hash)
);

-- Telemetry events for evals and conversion measurement.
CREATE TABLE IF NOT EXISTS {{schema_name}}.telemetry_events (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT,
    audit_run_id INTEGER REFERENCES {{schema_name}}.audit_runs(id),
    event_type TEXT NOT NULL,
    event_payload JSONB,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- v1 indexes (§10.3).
CREATE INDEX IF NOT EXISTS audit_findings_audit_run_id_idx
    ON {{schema_name}}.audit_findings(audit_run_id);

CREATE INDEX IF NOT EXISTS recommendations_audit_run_id_idx
    ON {{schema_name}}.recommendations(audit_run_id);

CREATE INDEX IF NOT EXISTS telemetry_events_audit_run_id_idx
    ON {{schema_name}}.telemetry_events(audit_run_id);
