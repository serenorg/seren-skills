#!/usr/bin/env python3
"""Normalized trading persistence helpers for SerenDB-backed skills."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
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


def _clone_jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _trade_reports_path() -> Path:
    configured = (os.getenv("TRADE_REPORTS_PATH") or "").strip()
    if configured:
        return Path(configured)
    return Path("logs") / "trade_reports.jsonl"


def _latest_row(rows: list[dict[str, Any]], *, time_key: str) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: str(row.get(time_key) or ""))


def _metadata_dict(row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    metadata = row.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _first_numeric(*values: Any) -> float | None:
    for value in values:
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _format_money(value: float | None) -> str:
    return "-" if value is None else f"${value:,.2f}"


def _format_qty(value: float | None) -> str:
    return "-" if value is None else f"{value:,.4f}"


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "  (none)"
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))
    header_line = "  " + " | ".join(header.ljust(widths[idx]) for idx, header in enumerate(headers))
    divider = "  " + "-+-".join("-" * width for width in widths)
    body = [
        "  " + " | ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row))
        for row in rows
    ]
    return "\n".join([header_line, divider, *body])


def _extract_halt_reason(
    *,
    status: str,
    summary: dict[str, Any],
    metadata: dict[str, Any],
    error_code: str | None,
    error_message: str | None,
) -> str | None:
    for candidate in (
        error_message,
        error_code,
        summary.get("halt_reason"),
        summary.get("blocked_reason"),
        summary.get("reason"),
        metadata.get("halt_reason"),
        metadata.get("blocked_reason"),
        metadata.get("reason"),
    ):
        if candidate not in (None, ""):
            return str(candidate)
    if status in {"failed", "blocked", "stopped"}:
        return status
    return None


def _extract_breach_positions(summary: dict[str, Any], metadata: dict[str, Any]) -> list[str]:
    results: list[str] = []
    for payload in (summary, metadata):
        for key in (
            "breach_positions",
            "positions_triggered_breach",
            "positions_breaching",
            "breached_positions",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    text = _first_non_empty(
                        item.get("symbol") if isinstance(item, dict) else None,
                        item.get("ticker") if isinstance(item, dict) else None,
                        item.get("position_key") if isinstance(item, dict) else None,
                        item,
                    )
                    if text and text not in results:
                        results.append(text)
        live_risk = payload.get("live_risk")
        if isinstance(live_risk, dict):
            for key in ("breach_positions", "positions_breaching", "breached_positions", "blocked_positions"):
                value = live_risk.get(key)
                if isinstance(value, list):
                    for item in value:
                        text = _first_non_empty(
                            item.get("symbol") if isinstance(item, dict) else None,
                            item.get("ticker") if isinstance(item, dict) else None,
                            item,
                        )
                        if text and text not in results:
                            results.append(text)
    return results


class NormalizedTradingStore:
    """Best-effort normalized trading persistence for Postgres-backed SerenDB skills."""

    def __init__(self, dsn: str | None, *, skill_slug: str, venue: str, strategy_name: str) -> None:
        self.dsn = (dsn or "").strip()
        self.skill_slug = skill_slug
        self.venue = venue
        self.strategy_name = strategy_name
        self.conn = None
        self._run_buffers: dict[str, dict[str, Any]] = {}

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
        resolved_run_id = run_id or str(uuid4())
        buffer = self._run_buffers.setdefault(
            resolved_run_id,
            {
                "order_events": [],
                "fills": [],
                "positions": {},
                "position_marks": [],
                "pnl_periods": [],
            },
        )
        buffer.update(
            {
                "run_id": resolved_run_id,
                "mode": mode,
                "dry_run": bool(dry_run),
                "status": status,
                "started_at": started_at or _now_iso(),
                "config": _clone_jsonable(config or {}),
                "summary": _clone_jsonable(summary or {}),
                "metadata": _clone_jsonable(metadata or {}),
                "error_code": error_code,
                "error_message": error_message,
                "completed_at": buffer.get("completed_at"),
            }
        )
        if not self.enabled:
            return resolved_run_id
        self.ensure_schema()
        self.connect()
        assert self.conn is not None
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
        buffer = self._run_buffers.setdefault(
            run_id,
            {
                "run_id": run_id,
                "order_events": [],
                "fills": [],
                "positions": {},
                "position_marks": [],
                "pnl_periods": [],
                "summary": {},
                "metadata": {},
            },
        )
        merged_summary = dict(buffer.get("summary") or {})
        merged_summary.update(_clone_jsonable(summary or {}))
        merged_metadata = dict(buffer.get("metadata") or {})
        merged_metadata.update(_clone_jsonable(metadata or {}))
        buffer.update(
            {
                "status": status,
                "completed_at": completed_at or _now_iso(),
                "summary": merged_summary,
                "metadata": merged_metadata,
                "error_code": error_code or buffer.get("error_code"),
                "error_message": error_message or buffer.get("error_message"),
            }
        )
        if self.enabled:
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
        self._emit_trade_report(run_id)

    def insert_order_events(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        buffered_rows: list[dict[str, Any]] = []
        prepared = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = _float_or_none(row.get("price"))
            quantity = _float_or_none(row.get("quantity"))
            notional = _float_or_none(row.get("notional_usd"))
            if notional is None and price is not None and quantity is not None:
                notional = price * quantity
            event_time = row.get("event_time") or _now_iso()
            buffered_rows.append(
                {
                    "order_id": row.get("order_id"),
                    "instrument_id": row.get("instrument_id"),
                    "symbol": row.get("symbol"),
                    "side": row.get("side"),
                    "order_type": row.get("order_type"),
                    "event_type": row.get("event_type") or row.get("status") or "order_event",
                    "status": row.get("status"),
                    "price": price,
                    "quantity": quantity,
                    "notional_usd": notional,
                    "event_time": event_time,
                    "metadata": _clone_jsonable(row.get("metadata") or {}),
                }
            )
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
                    event_time,
                    _json(row.get("metadata")),
                )
            )
        buffer = self._run_buffers.setdefault(
            run_id,
            {"order_events": [], "fills": [], "positions": {}, "position_marks": [], "pnl_periods": []},
        )
        buffer.setdefault("order_events", []).extend(buffered_rows)
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
        buffered_rows: list[dict[str, Any]] = []
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
            fill_time = row.get("fill_time") or row.get("event_time") or _now_iso()
            fee = _float_or_none(row.get("fee_usd") if "fee_usd" in row else row.get("fee"))
            realized_pnl = _float_or_none(
                row.get("realized_pnl_usd") if "realized_pnl_usd" in row else row.get("realized_pnl")
            )
            buffered_rows.append(
                {
                    "order_id": row.get("order_id"),
                    "venue_fill_id": row.get("venue_fill_id"),
                    "instrument_id": row.get("instrument_id"),
                    "symbol": row.get("symbol"),
                    "side": row.get("side"),
                    "fill_price": price,
                    "fill_quantity": quantity,
                    "fee_usd": fee,
                    "notional_usd": notional,
                    "realized_pnl_usd": realized_pnl,
                    "fill_time": fill_time,
                    "metadata": _clone_jsonable(row.get("metadata") or {}),
                }
            )
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
                    fee,
                    notional,
                    realized_pnl,
                    fill_time,
                    _json(row.get("metadata")),
                )
            )
        buffer = self._run_buffers.setdefault(
            run_id,
            {"order_events": [], "fills": [], "positions": {}, "position_marks": [], "pnl_periods": []},
        )
        buffer.setdefault("fills", []).extend(buffered_rows)
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
        buffered_positions = self._run_buffers.setdefault(
            run_id,
            {"order_events": [], "fills": [], "positions": {}, "position_marks": [], "pnl_periods": []},
        ).setdefault("positions", {})
        prepared = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            price = _float_or_none(row.get("market_price") if "market_price" in row else row.get("mark_price"))
            quantity = _float_or_none(row.get("quantity"))
            market_value = _float_or_none(row.get("market_value_usd") if "market_value_usd" in row else row.get("market_value"))
            if market_value is None and price is not None and quantity is not None:
                market_value = price * quantity
            position_key = _position_key(row, f"position-{index}")
            buffered_positions[position_key] = {
                "position_key": position_key,
                "instrument_id": row.get("instrument_id"),
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "quantity": quantity,
                "entry_price": _float_or_none(row.get("entry_price")),
                "cost_basis_usd": _float_or_none(
                    row.get("cost_basis_usd") if "cost_basis_usd" in row else row.get("cost_basis")
                ),
                "market_price": price,
                "market_value_usd": market_value,
                "unrealized_pnl_usd": _float_or_none(
                    row.get("unrealized_pnl_usd") if "unrealized_pnl_usd" in row else row.get("unrealized_pnl")
                ),
                "realized_pnl_usd": _float_or_none(
                    row.get("realized_pnl_usd") if "realized_pnl_usd" in row else row.get("realized_pnl")
                ),
                "status": row.get("status"),
                "opened_at": row.get("opened_at"),
                "closed_at": row.get("closed_at"),
                "metadata": _clone_jsonable(row.get("metadata") or {}),
            }
            prepared.append(
                (
                    run_id,
                    position_key,
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
        buffered_marks = self._run_buffers.setdefault(
            run_id,
            {"order_events": [], "fills": [], "positions": {}, "position_marks": [], "pnl_periods": []},
        ).setdefault("position_marks", [])
        prepared = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            price = _float_or_none(row.get("mark_price") if "mark_price" in row else row.get("market_price"))
            quantity = _float_or_none(row.get("quantity"))
            market_value = _float_or_none(row.get("market_value_usd") if "market_value_usd" in row else row.get("market_value"))
            if market_value is None and price is not None and quantity is not None:
                market_value = price * quantity
            position_key = _position_key(row, f"position-mark-{index}")
            mark_time = row.get("mark_time") or _now_iso()
            buffered_marks.append(
                {
                    "position_key": position_key,
                    "instrument_id": row.get("instrument_id"),
                    "symbol": row.get("symbol"),
                    "side": row.get("side"),
                    "quantity": quantity,
                    "mark_price": price,
                    "market_value_usd": market_value,
                    "unrealized_pnl_usd": _float_or_none(
                        row.get("unrealized_pnl_usd") if "unrealized_pnl_usd" in row else row.get("unrealized_pnl")
                    ),
                    "realized_pnl_usd": _float_or_none(
                        row.get("realized_pnl_usd") if "realized_pnl_usd" in row else row.get("realized_pnl")
                    ),
                    "mark_time": mark_time,
                    "metadata": _clone_jsonable(row.get("metadata") or {}),
                }
            )
            prepared.append(
                (
                    run_id,
                    position_key,
                    row.get("instrument_id"),
                    row.get("symbol"),
                    row.get("side"),
                    quantity,
                    price,
                    market_value,
                    _float_or_none(row.get("unrealized_pnl_usd") if "unrealized_pnl_usd" in row else row.get("unrealized_pnl")),
                    _float_or_none(row.get("realized_pnl_usd") if "realized_pnl_usd" in row else row.get("realized_pnl")),
                    mark_time,
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
        buffered_periods = self._run_buffers.setdefault(
            run_id,
            {"order_events": [], "fills": [], "positions": {}, "position_marks": [], "pnl_periods": []},
        ).setdefault("pnl_periods", [])
        prepared = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            period = {
                "period_type": row.get("period_type") or "run",
                "period_start": row.get("period_start"),
                "period_end": row.get("period_end") or _now_iso(),
                "realized_pnl_usd": _float_or_none(
                    row.get("realized_pnl_usd") if "realized_pnl_usd" in row else row.get("realized_pnl")
                ),
                "unrealized_pnl_usd": _float_or_none(
                    row.get("unrealized_pnl_usd") if "unrealized_pnl_usd" in row else row.get("unrealized_pnl")
                ),
                "fees_usd": _float_or_none(row.get("fees_usd") if "fees_usd" in row else row.get("fees")),
                "gross_pnl_usd": _float_or_none(
                    row.get("gross_pnl_usd") if "gross_pnl_usd" in row else row.get("gross_pnl")
                ),
                "net_pnl_usd": _float_or_none(row.get("net_pnl_usd") if "net_pnl_usd" in row else row.get("net_pnl")),
                "equity_start_usd": _float_or_none(
                    row.get("equity_start_usd") if "equity_start_usd" in row else row.get("equity_start")
                ),
                "equity_end_usd": _float_or_none(
                    row.get("equity_end_usd") if "equity_end_usd" in row else row.get("equity_end")
                ),
                "metadata": _clone_jsonable(row.get("metadata") or {}),
            }
            buffered_periods.append(period)
            prepared.append(
                (
                    run_id,
                    period["period_type"],
                    period["period_start"],
                    period["period_end"],
                    period["realized_pnl_usd"],
                    period["unrealized_pnl_usd"],
                    period["fees_usd"],
                    period["gross_pnl_usd"],
                    period["net_pnl_usd"],
                    period["equity_start_usd"],
                    period["equity_end_usd"],
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

    def _emit_trade_report(self, run_id: str) -> None:
        buffer = self._run_buffers.pop(run_id, None)
        if not isinstance(buffer, dict):
            return
        try:
            report = self._build_trade_report(buffer)
            path = _trade_reports_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(report, default=str, sort_keys=True) + "\n")
            if os.getenv("PYTHONUNBUFFERED") == "1":
                self._print_trade_report(report)
        except Exception as exc:  # pragma: no cover - defensive logging path
            print(f"[trade-report] failed to emit report for {run_id}: {exc}")

    def _build_trade_report(self, buffer: dict[str, Any]) -> dict[str, Any]:
        order_events = [row for row in buffer.get("order_events", []) if isinstance(row, dict)]
        fills = [row for row in buffer.get("fills", []) if isinstance(row, dict)]
        positions = [
            row
            for row in (buffer.get("positions") or {}).values()
            if isinstance(row, dict)
        ]
        position_marks = [row for row in buffer.get("position_marks", []) if isinstance(row, dict)]
        pnl_periods = [row for row in buffer.get("pnl_periods", []) if isinstance(row, dict)]
        summary = buffer.get("summary") if isinstance(buffer.get("summary"), dict) else {}
        metadata = buffer.get("metadata") if isinstance(buffer.get("metadata"), dict) else {}

        order_by_id = {
            str(row.get("order_id")): row
            for row in order_events
            if row.get("order_id") not in (None, "")
        }
        position_by_key: dict[str, dict[str, Any]] = {}
        for row in positions:
            for candidate in (row.get("position_key"), row.get("instrument_id"), row.get("symbol")):
                if candidate not in (None, ""):
                    position_by_key[str(candidate)] = row
        latest_marks: dict[str, dict[str, Any]] = {}
        for row in position_marks:
            for candidate in (row.get("position_key"), row.get("instrument_id"), row.get("symbol")):
                if candidate in (None, ""):
                    continue
                key = str(candidate)
                if key not in latest_marks or str(row.get("mark_time") or "") >= str(latest_marks[key].get("mark_time") or ""):
                    latest_marks[key] = row

        trades: list[dict[str, Any]] = []
        total_fees = 0.0
        total_realized = 0.0
        for fill in fills:
            fee_usd = _first_numeric(fill.get("fee_usd"), _metadata_dict(fill).get("fee_usd")) or 0.0
            total_fees += fee_usd
            realized_pnl = _first_numeric(fill.get("realized_pnl_usd"), _metadata_dict(fill).get("realized_pnl"))
            if realized_pnl is not None:
                total_realized += realized_pnl
            order_event = order_by_id.get(str(fill.get("order_id")))
            metadata_row = {}
            metadata_row.update(_metadata_dict(order_event))
            metadata_row.update(_metadata_dict(fill))
            instrument_id = _first_non_empty(
                fill.get("instrument_id"),
                order_event.get("instrument_id") if isinstance(order_event, dict) else None,
                metadata_row.get("market_id"),
            )
            symbol = _first_non_empty(
                fill.get("symbol"),
                order_event.get("symbol") if isinstance(order_event, dict) else None,
                metadata_row.get("ticker"),
                instrument_id,
            )
            position = None
            for candidate in (fill.get("instrument_id"), fill.get("symbol"), instrument_id, symbol):
                if candidate in (None, ""):
                    continue
                position = position_by_key.get(str(candidate))
                if position is not None:
                    break
            latest_mark = None
            for candidate in (fill.get("instrument_id"), fill.get("symbol"), instrument_id, symbol):
                if candidate in (None, ""):
                    continue
                latest_mark = latest_marks.get(str(candidate))
                if latest_mark is not None:
                    break
            fill_price = _first_numeric(fill.get("fill_price"), fill.get("price"))
            entry_price = _first_numeric(
                metadata_row.get("entry_price"),
                position.get("entry_price") if isinstance(position, dict) else None,
            )
            is_close = _first_non_empty(metadata_row.get("close_reason"), metadata_row.get("open_order_ref")) is not None
            if entry_price is None and not is_close:
                entry_price = fill_price
            current_price = _first_numeric(
                position.get("market_price") if isinstance(position, dict) else None,
                latest_mark.get("mark_price") if isinstance(latest_mark, dict) else None,
                metadata_row.get("current_price"),
                metadata_row.get("mark_price"),
            )
            trades.append(
                {
                    "order_id": fill.get("order_id"),
                    "market_id": instrument_id,
                    "market": _first_non_empty(
                        metadata_row.get("question"),
                        metadata_row.get("market"),
                        metadata_row.get("title"),
                        metadata_row.get("name"),
                        symbol,
                    ),
                    "symbol": symbol,
                    "side": _first_non_empty(fill.get("side"), order_event.get("side") if isinstance(order_event, dict) else None),
                    "quantity": _first_numeric(fill.get("fill_quantity"), fill.get("quantity")),
                    "entry_price": entry_price,
                    "exit_price": fill_price if is_close else None,
                    "current_price": current_price,
                    "fill_price": fill_price,
                    "realized_pnl_usd": realized_pnl,
                    "unrealized_pnl_usd": _first_numeric(
                        position.get("unrealized_pnl_usd") if isinstance(position, dict) else None,
                        latest_mark.get("unrealized_pnl_usd") if isinstance(latest_mark, dict) else None,
                    ),
                    "fee_usd": fee_usd,
                    "fill_time": fill.get("fill_time"),
                    "metadata": metadata_row,
                }
            )

        open_positions: list[dict[str, Any]] = []
        total_unrealized = 0.0
        for position in positions:
            quantity = _first_numeric(position.get("quantity"))
            status = str(position.get("status") or "").lower()
            if status == "closed":
                continue
            if quantity is not None and abs(quantity) <= 1e-12 and status not in {"open", "active"}:
                continue
            latest_mark = None
            for candidate in (position.get("position_key"), position.get("instrument_id"), position.get("symbol")):
                if candidate in (None, ""):
                    continue
                latest_mark = latest_marks.get(str(candidate))
                if latest_mark is not None:
                    break
            unrealized = _first_numeric(
                position.get("unrealized_pnl_usd"),
                latest_mark.get("unrealized_pnl_usd") if isinstance(latest_mark, dict) else None,
            )
            if unrealized is not None:
                total_unrealized += unrealized
            open_positions.append(
                {
                    "position_key": position.get("position_key"),
                    "market_id": _first_non_empty(position.get("instrument_id"), position.get("symbol")),
                    "market": _first_non_empty(
                        _metadata_dict(position).get("question"),
                        _metadata_dict(position).get("market"),
                        position.get("symbol"),
                        position.get("instrument_id"),
                    ),
                    "symbol": _first_non_empty(position.get("symbol"), position.get("instrument_id")),
                    "side": position.get("side"),
                    "quantity": quantity,
                    "entry_price": _first_numeric(position.get("entry_price")),
                    "current_price": _first_numeric(
                        position.get("market_price"),
                        latest_mark.get("mark_price") if isinstance(latest_mark, dict) else None,
                    ),
                    "market_value_usd": _first_numeric(
                        position.get("market_value_usd"),
                        latest_mark.get("market_value_usd") if isinstance(latest_mark, dict) else None,
                    ),
                    "realized_pnl_usd": _first_numeric(position.get("realized_pnl_usd")),
                    "unrealized_pnl_usd": unrealized,
                    "status": position.get("status"),
                    "opened_at": position.get("opened_at"),
                    "metadata": _metadata_dict(position),
                }
            )

        latest_pnl = _latest_row(pnl_periods, time_key="period_end")
        equity_end = _first_numeric(
            latest_pnl.get("equity_end_usd") if isinstance(latest_pnl, dict) else None,
            summary.get("current_equity_usd"),
            metadata.get("current_equity_usd"),
            (metadata.get("live_risk") or {}).get("current_equity_usd") if isinstance(metadata.get("live_risk"), dict) else None,
        )
        equity_start = _first_numeric(
            latest_pnl.get("equity_start_usd") if isinstance(latest_pnl, dict) else None,
            summary.get("starting_bankroll_usd"),
            metadata.get("starting_bankroll_usd"),
        )
        realized_pnl = _first_numeric(
            latest_pnl.get("realized_pnl_usd") if isinstance(latest_pnl, dict) else None,
            summary.get("realized_pnl_usd"),
            metadata.get("realized_pnl_usd"),
            total_realized,
        )
        unrealized_pnl = _first_numeric(
            latest_pnl.get("unrealized_pnl_usd") if isinstance(latest_pnl, dict) else None,
            summary.get("unrealized_pnl_usd"),
            metadata.get("unrealized_pnl_usd"),
            total_unrealized,
        )
        fees_usd = _first_numeric(
            latest_pnl.get("fees_usd") if isinstance(latest_pnl, dict) else None,
            summary.get("fees_usd"),
            metadata.get("fees_usd"),
            total_fees,
        )
        gross_pnl = _first_numeric(
            latest_pnl.get("gross_pnl_usd") if isinstance(latest_pnl, dict) else None,
            summary.get("gross_pnl_usd"),
            metadata.get("gross_pnl_usd"),
            (realized_pnl or 0.0) + (unrealized_pnl or 0.0),
        )
        net_pnl = _first_numeric(
            latest_pnl.get("net_pnl_usd") if isinstance(latest_pnl, dict) else None,
            summary.get("net_pnl_usd"),
            metadata.get("net_pnl_usd"),
            (gross_pnl or 0.0) - (fees_usd or 0.0),
        )

        previous_equity, peak_equity = self._history_stats(mode=str(buffer.get("mode") or ""))
        equity_change = None
        if equity_end is not None and previous_equity is not None:
            equity_change = equity_end - previous_equity
        peak_reference = max(value for value in (peak_equity, equity_end) if value is not None) if any(
            value is not None for value in (peak_equity, equity_end)
        ) else None
        drawdown_usd = _first_numeric(
            summary.get("max_drawdown"),
            metadata.get("max_drawdown"),
            latest_pnl.get("metadata", {}).get("max_drawdown") if isinstance(latest_pnl, dict) and isinstance(latest_pnl.get("metadata"), dict) else None,
        )
        if drawdown_usd is None and peak_reference is not None and equity_end is not None:
            drawdown_usd = max(peak_reference - equity_end, 0.0)
        drawdown_pct = _first_numeric(summary.get("max_drawdown_pct"), metadata.get("max_drawdown_pct"))
        if drawdown_pct is None and drawdown_usd is not None and peak_reference not in (None, 0.0):
            drawdown_pct = (drawdown_usd / peak_reference) * 100.0

        cycle_summary = {
            "realized_pnl_usd": realized_pnl,
            "unrealized_pnl_usd": unrealized_pnl,
            "fees_usd": fees_usd,
            "gross_pnl_usd": gross_pnl,
            "net_pnl_usd": net_pnl,
            "equity_start_usd": equity_start,
            "equity_end_usd": equity_end,
            "previous_equity_end_usd": previous_equity,
            "equity_change_vs_previous_cycle_usd": equity_change,
            "drawdown_usd": drawdown_usd,
            "drawdown_pct": drawdown_pct,
            "order_event_count": len(order_events),
            "fill_count": len(fills),
            "halt_reason": _extract_halt_reason(
                status=str(buffer.get("status") or ""),
                summary=summary,
                metadata=metadata,
                error_code=buffer.get("error_code"),
                error_message=buffer.get("error_message"),
            ),
            "breach_positions": _extract_breach_positions(summary, metadata),
        }

        return {
            "generated_at": _now_iso(),
            "run_id": buffer.get("run_id"),
            "skill_slug": self.skill_slug,
            "venue": self.venue,
            "strategy_name": self.strategy_name,
            "mode": buffer.get("mode"),
            "status": buffer.get("status"),
            "dry_run": bool(buffer.get("dry_run", True)),
            "started_at": buffer.get("started_at"),
            "completed_at": buffer.get("completed_at"),
            "summary": summary,
            "metadata": metadata,
            "cycle_summary": cycle_summary,
            "trades": trades,
            "open_positions": open_positions,
        }

    def _history_stats(self, *, mode: str) -> tuple[float | None, float | None]:
        path = _trade_reports_path()
        if not path.exists():
            return None, None
        previous_equity = None
        peak_equity = None
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("skill_slug") != self.skill_slug or entry.get("mode") != mode:
                    continue
                cycle_summary = entry.get("cycle_summary")
                if not isinstance(cycle_summary, dict):
                    continue
                equity = _float_or_none(cycle_summary.get("equity_end_usd"))
                if equity is None:
                    continue
                previous_equity = equity
                peak_equity = equity if peak_equity is None else max(peak_equity, equity)
        return previous_equity, peak_equity

    def _print_trade_report(self, report: dict[str, Any]) -> None:
        cycle_summary = report.get("cycle_summary", {})
        trades = report.get("trades", [])
        open_positions = report.get("open_positions", [])
        print(
            f"[trade-report] {self.skill_slug} "
            f"{report.get('mode')}/{report.get('status')} run={report.get('run_id')}"
        )
        print(
            "  realized="
            f"{_format_money(_float_or_none(cycle_summary.get('realized_pnl_usd')))} "
            "unrealized="
            f"{_format_money(_float_or_none(cycle_summary.get('unrealized_pnl_usd')))} "
            "fees="
            f"{_format_money(_float_or_none(cycle_summary.get('fees_usd')))} "
            "equity="
            f"{_format_money(_float_or_none(cycle_summary.get('equity_end_usd')))}"
        )
        print("  Trades:")
        print(
            _render_table(
                ["Market", "Side", "Qty", "Fill", "Current", "Realized", "Order"],
                [
                    [
                        str(row.get("market") or row.get("symbol") or "-"),
                        str(row.get("side") or "-"),
                        _format_qty(_float_or_none(row.get("quantity"))),
                        _format_money(_float_or_none(row.get("fill_price"))),
                        _format_money(_float_or_none(row.get("current_price"))),
                        _format_money(_float_or_none(row.get("realized_pnl_usd"))),
                        str(row.get("order_id") or "-"),
                    ]
                    for row in trades
                ],
            )
        )
        print("  Open Positions:")
        print(
            _render_table(
                ["Market", "Side", "Qty", "Entry", "Current", "Value", "Unrealized"],
                [
                    [
                        str(row.get("market") or row.get("symbol") or "-"),
                        str(row.get("side") or "-"),
                        _format_qty(_float_or_none(row.get("quantity"))),
                        _format_money(_float_or_none(row.get("entry_price"))),
                        _format_money(_float_or_none(row.get("current_price"))),
                        _format_money(_float_or_none(row.get("market_value_usd"))),
                        _format_money(_float_or_none(row.get("unrealized_pnl_usd"))),
                    ]
                    for row in open_positions
                ],
            )
        )

    def _executemany(self, query: str, rows: list[tuple[Any, ...]]) -> None:
        if not rows or not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.executemany(query, rows)
        self.conn.commit()
