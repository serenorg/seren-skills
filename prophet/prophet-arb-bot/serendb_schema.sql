-- prophet-arb-bot SerenDB schema (v1).
--
-- Lives in project=prophet, database=prophet. Every table is created with
-- IF NOT EXISTS so re-running the bootstrap is safe.

-- ---------------------------------------------------------------------------
-- arb_pairs — prophet ↔ polymarket pair binding the arb-bot trades.
-- Seeded by the operator (inputs.manual_pairs in config.json) or by
-- auto-discover during a `--command run` cycle.

CREATE TABLE IF NOT EXISTS arb_pairs (
    prophet_market_id            TEXT PRIMARY KEY,
    polymarket_condition_id      TEXT NOT NULL,
    source_skill                 TEXT NOT NULL DEFAULT 'manual',
    last_seen_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS arb_pairs_polymarket_idx
    ON arb_pairs (polymarket_condition_id);

-- ---------------------------------------------------------------------------
-- arb_runs — one row per `agent.py --command run` invocation.

CREATE TABLE IF NOT EXISTS arb_runs (
    run_id                       TEXT PRIMARY KEY,
    mode                         TEXT NOT NULL,
    status                       TEXT NOT NULL,
    summary                      JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at                  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS arb_runs_started_at_idx
    ON arb_runs (started_at DESC);

-- ---------------------------------------------------------------------------
-- arb_opportunities — every scored opportunity, acted on or not.
-- Records the decision input even when no trade was placed, so the
-- operator can replay a cycle and understand why the agent skipped.

CREATE TABLE IF NOT EXISTS arb_opportunities (
    id                           BIGSERIAL PRIMARY KEY,
    run_id                       TEXT NOT NULL REFERENCES arb_runs (run_id) ON DELETE CASCADE,
    prophet_market_id            TEXT NOT NULL,
    polymarket_condition_id      TEXT NOT NULL,
    side                         TEXT NOT NULL,
    outcome                      TEXT NOT NULL,
    spread                       DOUBLE PRECISION NOT NULL,
    edge                         DOUBLE PRECISION NOT NULL,
    size_usdc                    DOUBLE PRECISION NOT NULL,
    limit_price                  DOUBLE PRECISION NOT NULL,
    reason                       TEXT NOT NULL,
    health_warnings              JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS arb_opportunities_run_idx
    ON arb_opportunities (run_id);
CREATE INDEX IF NOT EXISTS arb_opportunities_market_idx
    ON arb_opportunities (prophet_market_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- arb_orders — orders we submitted to prophet.

CREATE TABLE IF NOT EXISTS arb_orders (
    prophet_order_id             TEXT PRIMARY KEY,
    run_id                       TEXT NOT NULL,
    prophet_market_id            TEXT NOT NULL,
    side                         TEXT NOT NULL,
    outcome                      TEXT NOT NULL,
    shares                       DOUBLE PRECISION NOT NULL,
    limit_price                  DOUBLE PRECISION NOT NULL,
    status                       TEXT NOT NULL,
    last_seen_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS arb_orders_market_idx
    ON arb_orders (prophet_market_id, created_at DESC);
CREATE INDEX IF NOT EXISTS arb_orders_status_idx
    ON arb_orders (status);

-- Delta-neutral (#536) — track the Polymarket hedge leg alongside the
-- Prophet leg. Single-leg rows leave these columns NULL / default; the
-- recorder only writes them when execution_mode = "delta_neutral".
ALTER TABLE arb_orders
    ADD COLUMN IF NOT EXISTS polymarket_filled_qty   DOUBLE PRECISION DEFAULT 0,
    ADD COLUMN IF NOT EXISTS polymarket_fill_price   DOUBLE PRECISION DEFAULT 0,
    ADD COLUMN IF NOT EXISTS polymarket_order_id     TEXT,
    ADD COLUMN IF NOT EXISTS hedge_status            TEXT DEFAULT 'pending';

ALTER TABLE arb_orders
    DROP CONSTRAINT IF EXISTS arb_orders_hedge_status_check;

ALTER TABLE arb_orders
    ADD CONSTRAINT arb_orders_hedge_status_check
    CHECK (hedge_status IS NULL OR hedge_status IN (
        'pending',
        'hedged',
        'naked_exposure',
        'unwound',
        'hedge_failed_no_commit',
        'unwound_after_prophet_decline'
    ));

CREATE INDEX IF NOT EXISTS arb_orders_hedge_status_idx
    ON arb_orders (hedge_status);

-- ---------------------------------------------------------------------------
-- arb_positions — open holdings (computed from fills).

CREATE TABLE IF NOT EXISTS arb_positions (
    prophet_market_id            TEXT NOT NULL,
    outcome                      TEXT NOT NULL,
    shares                       DOUBLE PRECISION NOT NULL DEFAULT 0,
    avg_cost                     DOUBLE PRECISION NOT NULL DEFAULT 0,
    realized_pnl_usdc            DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (prophet_market_id, outcome)
);

-- ---------------------------------------------------------------------------
-- arb_pnl_snapshots — daily mark-to-market against polymarket prices.

CREATE TABLE IF NOT EXISTS arb_pnl_snapshots (
    snapshot_date                DATE NOT NULL,
    prophet_market_id            TEXT NOT NULL,
    outcome                      TEXT NOT NULL,
    shares                       DOUBLE PRECISION NOT NULL,
    avg_cost                     DOUBLE PRECISION NOT NULL,
    polymarket_mark              DOUBLE PRECISION NOT NULL,
    unrealized_pnl_usdc          DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (snapshot_date, prophet_market_id, outcome)
);

CREATE INDEX IF NOT EXISTS arb_pnl_snapshots_date_idx
    ON arb_pnl_snapshots (snapshot_date DESC);
