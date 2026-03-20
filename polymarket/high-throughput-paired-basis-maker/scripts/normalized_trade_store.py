#!/usr/bin/env python3
"""Normalized trading persistence helpers for SerenDB-backed skills."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS trading;

CREATE TABLE IF NOT EXISTS trading.strategy_runs (
    run_id UUID PRIMARY KEY,
    skill_slug TEXT NOT NULL,
    venue TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    dry_run BOOLEAN NOT NULL DEFAULT TRUE,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_code TEXT,
    error_message TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_strategy_runs_skill_mode_started
    ON trading.strategy_runs (skill_slug, mode, started_at DESC);

CREATE TABLE IF NOT EXISTS trading.order_events (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
    order_id TEXT,
    instrument_id TEXT,
    symbol TEXT,
    side TEXT,
    order_type TEXT,
    event_type TEXT NOT NULL,
    status TEXT,
    price NUMERIC(24, 10),
    quantity NUMERIC(24, 10),
    notional_usd NUMERIC(24, 10),
    event_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_order_events_run_time
    ON trading.order_events (run_id, event_time DESC);

CREATE TABLE IF NOT EXISTS trading.fills (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
    order_id TEXT,
    venue_fill_id TEXT,
    instrument_id TEXT,
    symbol TEXT,
    side TEXT,
    fill_price NUMERIC(24, 10),
    fill_quantity NUMERIC(24, 10),
    fee_usd NUMERIC(24, 10),
    notional_usd NUMERIC(24, 10),
    realized_pnl_usd NUMERIC(24, 10),
    fill_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_fills_run_time
    ON trading.fills (run_id, fill_time DESC);

CREATE TABLE IF NOT EXISTS trading.positions (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
    position_key TEXT NOT NULL,
    instrument_id TEXT,
    symbol TEXT,
    side TEXT,
    quantity NUMERIC(24, 10),
    entry_price NUMERIC(24, 10),
    cost_basis_usd NUMERIC(24, 10),
    market_price NUMERIC(24, 10),
    market_value_usd NUMERIC(24, 10),
    unrealized_pnl_usd NUMERIC(24, 10),
    realized_pnl_usd NUMERIC(24, 10),
    status TEXT,
    opened_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (run_id, position_key)
);

CREATE INDEX IF NOT EXISTS idx_positions_run_status
    ON trading.positions (run_id, status);

CREATE TABLE IF NOT EXISTS trading.position_marks (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
    position_key TEXT NOT NULL,
    instrument_id TEXT,
    symbol TEXT,
    side TEXT,
    quantity NUMERIC(24, 10),
    mark_price NUMERIC(24, 10),
    market_value_usd NUMERIC(24, 10),
    unrealized_pnl_usd NUMERIC(24, 10),
    realized_pnl_usd NUMERIC(24, 10),
    mark_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_position_marks_run_time
    ON trading.position_marks (run_id, mark_time DESC);

CREATE TABLE IF NOT EXISTS trading.pnl_periods (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES trading.strategy_runs(run_id) ON DELETE CASCADE,
    period_type TEXT NOT NULL,
    period_start TIMESTAMPTZ,
    period_end TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    realized_pnl_usd NUMERIC(24, 10),
    unrealized_pnl_usd NUMERIC(24, 10),
    fees_usd NUMERIC(24, 10),
    gross_pnl_usd NUMERIC(24, 10),
    net_pnl_usd NUMERIC(24, 10),
    equity_start_usd NUMERIC(24, 10),
    equity_end_usd NUMERIC(24, 10),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_pnl_periods_run_end
    ON trading.pnl_periods (run_id, period_end DESC);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, default=str)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _position_key(row: dict[str, Any], fallback: str) -> str:
    for key in ("position_key", "symbol", "instrument_id", "order_id", "asset", "ticker", "market_id"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return fallback


class NormalizedTradingStore:
    """Best-effort normalized trading persistence for Postgres-backed SerenDB skills."""

    def __init__(self, dsn: str | None, *, skill_slug: str, venue: str, strategy_name: str) -> None:
        self.dsn = (dsn or "").strip()
        self.skill_slug = skill_slug
        self.venue = venue
        self.strategy_name = strategy_name
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

    def start_run(
        self,
        *,
        run_id: str | None = None,
        mode: str,
        dry_run: bool,
        config: dict[str, Any],
        status: str = "running",
        started_at: str | None = None,
        summary: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> str | None:
        if not self.enabled:
            return None
        self.ensure_schema()
        self.connect()
        assert self.conn is not None
        resolved_run_id = run_id or str(uuid4())
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO trading.strategy_runs (
                    run_id, skill_slug, venue, strategy_name, mode, status,
                    dry_run, started_at, config, summary, error_code, error_message, metadata
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb
                )
                ON CONFLICT (run_id) DO UPDATE SET
                    skill_slug = EXCLUDED.skill_slug,
                    venue = EXCLUDED.venue,
                    strategy_name = EXCLUDED.strategy_name,
                    mode = EXCLUDED.mode,
                    status = EXCLUDED.status,
                    dry_run = EXCLUDED.dry_run,
                    started_at = EXCLUDED.started_at,
                    config = EXCLUDED.config,
                    summary = EXCLUDED.summary,
                    error_code = EXCLUDED.error_code,
                    error_message = EXCLUDED.error_message,
                    metadata = EXCLUDED.metadata
                """,
                (
                    resolved_run_id,
                    self.skill_slug,
                    self.venue,
                    self.strategy_name,
                    mode,
                    status,
                    bool(dry_run),
                    started_at or _now_iso(),
                    _json(config),
                    _json(summary),
                    error_code,
                    error_message,
                    _json(metadata),
                ),
            )
        self.conn.commit()
        return resolved_run_id

    def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        summary: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        completed_at: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE trading.strategy_runs
                SET status = %s,
                    completed_at = %s,
                    summary = COALESCE(summary, '{}'::jsonb) || %s::jsonb,
                    metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb,
                    error_code = COALESCE(%s, error_code),
                    error_message = COALESCE(%s, error_message)
                WHERE run_id = %s
                """,
                (
                    status,
                    completed_at or _now_iso(),
                    _json(summary),
                    _json(metadata),
                    error_code,
                    error_message,
                    run_id,
                ),
            )
        self.conn.commit()

    def insert_order_events(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        prepared = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = _float_or_none(row.get("price"))
            quantity = _float_or_none(row.get("quantity"))
            notional = _float_or_none(row.get("notional_usd"))
            if notional is None and price is not None and quantity is not None:
                notional = price * quantity
            prepared.append(
                (
                    run_id,
                    row.get("order_id"),
                    row.get("instrument_id"),
                    row.get("symbol"),
                    row.get("side"),
                    row.get("order_type"),
                    row.get("event_type") or row.get("status") or "order_event",
                    row.get("status"),
                    price,
                    quantity,
                    notional,
                    row.get("event_time") or _now_iso(),
                    _json(row.get("metadata")),
                )
            )
        self._executemany(
            """
            INSERT INTO trading.order_events (
                run_id, order_id, instrument_id, symbol, side, order_type,
                event_type, status, price, quantity, notional_usd, event_time, metadata
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s::jsonb
            )
            """,
            prepared,
        )

    def insert_fills(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        prepared = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = _float_or_none(row.get("fill_price") if "fill_price" in row else row.get("price"))
            quantity = _float_or_none(
                row.get("fill_quantity")
                if "fill_quantity" in row
                else row.get("quantity")
            )
            notional = _float_or_none(row.get("notional_usd"))
            if notional is None and price is not None and quantity is not None:
                notional = price * quantity
            prepared.append(
                (
                    run_id,
                    row.get("order_id"),
                    row.get("venue_fill_id"),
                    row.get("instrument_id"),
                    row.get("symbol"),
                    row.get("side"),
                    price,
                    quantity,
                    _float_or_none(row.get("fee_usd") if "fee_usd" in row else row.get("fee")),
                    notional,
                    _float_or_none(row.get("realized_pnl_usd") if "realized_pnl_usd" in row else row.get("realized_pnl")),
                    row.get("fill_time") or row.get("event_time") or _now_iso(),
                    _json(row.get("metadata")),
                )
            )
        self._executemany(
            """
            INSERT INTO trading.fills (
                run_id, order_id, venue_fill_id, instrument_id, symbol, side,
                fill_price, fill_quantity, fee_usd, notional_usd, realized_pnl_usd, fill_time, metadata
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s::jsonb
            )
            """,
            prepared,
        )

    def upsert_positions(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        prepared = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            price = _float_or_none(row.get("market_price") if "market_price" in row else row.get("mark_price"))
            quantity = _float_or_none(row.get("quantity"))
            market_value = _float_or_none(row.get("market_value_usd") if "market_value_usd" in row else row.get("market_value"))
            if market_value is None and price is not None and quantity is not None:
                market_value = price * quantity
            prepared.append(
                (
                    run_id,
                    _position_key(row, f"position-{index}"),
                    row.get("instrument_id"),
                    row.get("symbol"),
                    row.get("side"),
                    quantity,
                    _float_or_none(row.get("entry_price")),
                    _float_or_none(row.get("cost_basis_usd") if "cost_basis_usd" in row else row.get("cost_basis")),
                    price,
                    market_value,
                    _float_or_none(row.get("unrealized_pnl_usd") if "unrealized_pnl_usd" in row else row.get("unrealized_pnl")),
                    _float_or_none(row.get("realized_pnl_usd") if "realized_pnl_usd" in row else row.get("realized_pnl")),
                    row.get("status"),
                    row.get("opened_at"),
                    row.get("closed_at"),
                    _json(row.get("metadata")),
                )
            )
        self._executemany(
            """
            INSERT INTO trading.positions (
                run_id, position_key, instrument_id, symbol, side, quantity,
                entry_price, cost_basis_usd, market_price, market_value_usd,
                unrealized_pnl_usd, realized_pnl_usd, status, opened_at, closed_at, metadata
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s::jsonb
            )
            ON CONFLICT (run_id, position_key) DO UPDATE SET
                instrument_id = EXCLUDED.instrument_id,
                symbol = EXCLUDED.symbol,
                side = EXCLUDED.side,
                quantity = EXCLUDED.quantity,
                entry_price = EXCLUDED.entry_price,
                cost_basis_usd = EXCLUDED.cost_basis_usd,
                market_price = EXCLUDED.market_price,
                market_value_usd = EXCLUDED.market_value_usd,
                unrealized_pnl_usd = EXCLUDED.unrealized_pnl_usd,
                realized_pnl_usd = EXCLUDED.realized_pnl_usd,
                status = EXCLUDED.status,
                opened_at = COALESCE(EXCLUDED.opened_at, trading.positions.opened_at),
                closed_at = COALESCE(EXCLUDED.closed_at, trading.positions.closed_at),
                metadata = EXCLUDED.metadata
            """,
            prepared,
        )

    def insert_position_marks(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        prepared = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            price = _float_or_none(row.get("mark_price") if "mark_price" in row else row.get("market_price"))
            quantity = _float_or_none(row.get("quantity"))
            market_value = _float_or_none(row.get("market_value_usd") if "market_value_usd" in row else row.get("market_value"))
            if market_value is None and price is not None and quantity is not None:
                market_value = price * quantity
            prepared.append(
                (
                    run_id,
                    _position_key(row, f"position-mark-{index}"),
                    row.get("instrument_id"),
                    row.get("symbol"),
                    row.get("side"),
                    quantity,
                    price,
                    market_value,
                    _float_or_none(row.get("unrealized_pnl_usd") if "unrealized_pnl_usd" in row else row.get("unrealized_pnl")),
                    _float_or_none(row.get("realized_pnl_usd") if "realized_pnl_usd" in row else row.get("realized_pnl")),
                    row.get("mark_time") or _now_iso(),
                    _json(row.get("metadata")),
                )
            )
        self._executemany(
            """
            INSERT INTO trading.position_marks (
                run_id, position_key, instrument_id, symbol, side, quantity,
                mark_price, market_value_usd, unrealized_pnl_usd, realized_pnl_usd, mark_time, metadata
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s::jsonb
            )
            """,
            prepared,
        )

    def insert_pnl_periods(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        prepared = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            prepared.append(
                (
                    run_id,
                    row.get("period_type") or "run",
                    row.get("period_start"),
                    row.get("period_end") or _now_iso(),
                    _float_or_none(row.get("realized_pnl_usd") if "realized_pnl_usd" in row else row.get("realized_pnl")),
                    _float_or_none(row.get("unrealized_pnl_usd") if "unrealized_pnl_usd" in row else row.get("unrealized_pnl")),
                    _float_or_none(row.get("fees_usd") if "fees_usd" in row else row.get("fees")),
                    _float_or_none(row.get("gross_pnl_usd") if "gross_pnl_usd" in row else row.get("gross_pnl")),
                    _float_or_none(row.get("net_pnl_usd") if "net_pnl_usd" in row else row.get("net_pnl")),
                    _float_or_none(row.get("equity_start_usd") if "equity_start_usd" in row else row.get("equity_start")),
                    _float_or_none(row.get("equity_end_usd") if "equity_end_usd" in row else row.get("equity_end")),
                    _json(row.get("metadata")),
                )
            )
        self._executemany(
            """
            INSERT INTO trading.pnl_periods (
                run_id, period_type, period_start, period_end,
                realized_pnl_usd, unrealized_pnl_usd, fees_usd,
                gross_pnl_usd, net_pnl_usd, equity_start_usd, equity_end_usd, metadata
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s::jsonb
            )
            """,
            prepared,
        )

    def persist_completed_run(
        self,
        *,
        mode: str,
        dry_run: bool,
        config: dict[str, Any],
        status: str,
        summary: dict[str, Any] | None = None,
        order_events: list[dict[str, Any]] | None = None,
        fills: list[dict[str, Any]] | None = None,
        positions: list[dict[str, Any]] | None = None,
        position_marks: list[dict[str, Any]] | None = None,
        pnl_periods: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        run_id: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> str | None:
        resolved_run_id = self.start_run(
            run_id=run_id,
            mode=mode,
            dry_run=dry_run,
            config=config,
            status="running",
            started_at=started_at,
            summary=summary,
            metadata=metadata,
            error_code=error_code,
            error_message=error_message,
        )
        if not resolved_run_id:
            return None
        self.insert_order_events(resolved_run_id, list(order_events or []))
        self.insert_fills(resolved_run_id, list(fills or []))
        self.upsert_positions(resolved_run_id, list(positions or []))
        self.insert_position_marks(resolved_run_id, list(position_marks or []))
        self.insert_pnl_periods(resolved_run_id, list(pnl_periods or []))
        self.finish_run(
            run_id=resolved_run_id,
            status=status,
            summary=summary,
            metadata=metadata,
            completed_at=completed_at,
            error_code=error_code,
            error_message=error_message,
        )
        return resolved_run_id

    def _executemany(self, query: str, rows: list[tuple[Any, ...]]) -> None:
        if not rows or not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.executemany(query, rows)
        self.conn.commit()
