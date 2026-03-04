#!/usr/bin/env python3
"""SerenDB persistence for execution history and analytics."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS dca_executions (
    id SERIAL PRIMARY KEY,
    execution_id TEXT UNIQUE NOT NULL,
    mode TEXT NOT NULL,
    asset TEXT NOT NULL,
    target_amount_usd NUMERIC(12,2) NOT NULL,
    executed_amount_usd NUMERIC(12,2),
    executed_price NUMERIC(18,8),
    vwap_at_execution NUMERIC(18,8),
    savings_vs_naive_bps INTEGER,
    strategy TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    executed_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    kraken_order_id TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_id TEXT UNIQUE NOT NULL,
    total_value_usd NUMERIC(12,2) NOT NULL,
    allocations JSONB NOT NULL,
    target_allocations JSONB NOT NULL,
    drift_max_pct NUMERIC(5,2),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scanner_signals (
    id SERIAL PRIMARY KEY,
    signal_id TEXT UNIQUE NOT NULL,
    signal_type TEXT NOT NULL,
    asset TEXT NOT NULL,
    confidence_pct NUMERIC(5,2),
    trigger_data JSONB NOT NULL,
    suggestion TEXT,
    reallocation_pct NUMERIC(5,2),
    user_action TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cost_basis_lots (
    id SERIAL PRIMARY KEY,
    lot_id TEXT UNIQUE NOT NULL,
    asset TEXT NOT NULL,
    quantity NUMERIC(18,8) NOT NULL,
    cost_basis_usd NUMERIC(12,2) NOT NULL,
    acquisition_date TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    execution_id TEXT,
    disposed BOOLEAN DEFAULT FALSE,
    disposed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dca_sessions (
    id SERIAL PRIMARY KEY,
    session_id TEXT UNIQUE NOT NULL,
    mode TEXT NOT NULL,
    config JSONB NOT NULL,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    total_invested_usd NUMERIC(12,2) DEFAULT 0,
    total_savings_bps INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active'
);
"""


class SerenDBStore:
    """Best-effort Postgres persistence layer for SerenDB."""

    def __init__(self, dsn: str | None) -> None:
        self.dsn = (dsn or "").strip()
        self.conn = None

    @property
    def enabled(self) -> bool:
        return bool(self.dsn) and psycopg is not None

    def connect(self) -> None:
        if not self.enabled:
            return
        if self.conn is None:
            self.conn = psycopg.connect(self.dsn)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def ensure_schema(self) -> bool:
        if not self.enabled:
            return False
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        self.conn.commit()
        return True

    def create_session(self, session_id: str, mode: str, config: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dca_sessions (session_id, mode, config)
                VALUES (%s, %s, %s)
                ON CONFLICT (session_id) DO NOTHING
                """,
                (session_id, mode, json.dumps(config)),
            )
        self.conn.commit()

    def close_session(
        self,
        *,
        session_id: str,
        status: str,
        total_invested_usd: float,
        total_savings_bps: int,
    ) -> None:
        if not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE dca_sessions
                SET ended_at = NOW(), status = %s,
                    total_invested_usd = %s,
                    total_savings_bps = %s
                WHERE session_id = %s
                """,
                (status, total_invested_usd, total_savings_bps, session_id),
            )
        self.conn.commit()

    def persist_execution(self, row: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO dca_executions (
                    execution_id, mode, asset, target_amount_usd,
                    executed_amount_usd, executed_price, vwap_at_execution,
                    savings_vs_naive_bps, strategy, window_start, window_end,
                    executed_at, status, kraken_order_id, metadata
                ) VALUES (
                    %(execution_id)s, %(mode)s, %(asset)s, %(target_amount_usd)s,
                    %(executed_amount_usd)s, %(executed_price)s, %(vwap_at_execution)s,
                    %(savings_vs_naive_bps)s, %(strategy)s, %(window_start)s, %(window_end)s,
                    %(executed_at)s, %(status)s, %(kraken_order_id)s, %(metadata)s
                )
                ON CONFLICT (execution_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    executed_amount_usd = EXCLUDED.executed_amount_usd,
                    executed_price = EXCLUDED.executed_price,
                    executed_at = EXCLUDED.executed_at,
                    kraken_order_id = EXCLUDED.kraken_order_id,
                    metadata = EXCLUDED.metadata
                """,
                {
                    **row,
                    "metadata": json.dumps(row.get("metadata", {})),
                },
            )
        self.conn.commit()

    def persist_portfolio_snapshot(self, row: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO portfolio_snapshots (
                    snapshot_id, total_value_usd, allocations, target_allocations, drift_max_pct
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (snapshot_id) DO NOTHING
                """,
                (
                    row["snapshot_id"],
                    row["total_value_usd"],
                    json.dumps(row["allocations"]),
                    json.dumps(row["target_allocations"]),
                    row["drift_max_pct"],
                ),
            )
        self.conn.commit()

    def persist_scanner_signal(self, row: dict[str, Any], user_action: str | None = None) -> None:
        if not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scanner_signals (
                    signal_id, signal_type, asset, confidence_pct,
                    trigger_data, suggestion, reallocation_pct, user_action
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (signal_id) DO UPDATE SET
                    user_action = EXCLUDED.user_action
                """,
                (
                    row["signal_id"],
                    row["signal_type"],
                    row["asset"],
                    row["confidence_pct"],
                    json.dumps(row["trigger_data"]),
                    row["suggestion"],
                    row["reallocation_pct"],
                    user_action,
                ),
            )
        self.conn.commit()

    def persist_cost_basis_lot(self, row: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO cost_basis_lots (
                    lot_id, asset, quantity, cost_basis_usd, acquisition_date,
                    source, execution_id, disposed, disposed_at
                ) VALUES (
                    %(lot_id)s, %(asset)s, %(quantity)s, %(cost_basis_usd)s, %(acquisition_date)s,
                    %(source)s, %(execution_id)s, %(disposed)s, %(disposed_at)s
                )
                ON CONFLICT (lot_id) DO NOTHING
                """,
                row,
            )
        self.conn.commit()
