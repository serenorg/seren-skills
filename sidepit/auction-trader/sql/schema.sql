-- Sidepit Auction Trader — SerenDB schema
-- All auction activity persisted for analysis and agent learning.

CREATE TABLE IF NOT EXISTS auction_orders (
    order_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    epoch           BIGINT NOT NULL,
    ticker          TEXT NOT NULL,
    side            SMALLINT NOT NULL,
    size            INTEGER NOT NULL,
    price           BIGINT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'submitted',
    dry_run         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auction_orders_run_id
    ON auction_orders(run_id);
CREATE INDEX IF NOT EXISTS idx_auction_orders_ticker_epoch
    ON auction_orders(ticker, epoch DESC);

CREATE TABLE IF NOT EXISTS auction_fills (
    fill_id         SERIAL PRIMARY KEY,
    order_id        TEXT NOT NULL REFERENCES auction_orders(order_id) ON DELETE CASCADE,
    aggressive_id   TEXT NOT NULL,
    passive_id      TEXT NOT NULL,
    price           BIGINT NOT NULL,
    qty             INTEGER NOT NULL,
    aggressive_side SMALLINT NOT NULL,
    filled_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auction_fills_order_id
    ON auction_fills(order_id);

CREATE TABLE IF NOT EXISTS market_snapshots (
    snapshot_id     SERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    epoch           BIGINT NOT NULL,
    bid             BIGINT,
    ask             BIGINT,
    last_price      BIGINT,
    last_size       INTEGER,
    depth_json      JSONB,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_ticker_epoch
    ON market_snapshots(ticker, epoch DESC);

CREATE TABLE IF NOT EXISTS position_snapshots (
    snapshot_id     SERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL,
    trader_id       TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    position_side   SMALLINT NOT NULL,
    avg_price       BIGINT NOT NULL,
    open_orders     INTEGER NOT NULL DEFAULT 0,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_position_snapshots_trader_ticker
    ON position_snapshots(trader_id, ticker, captured_at DESC);
