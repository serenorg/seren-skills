"""Best-effort Postgres persistence layer for Sidepit auction activity."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    import psycopg  # type: ignore[import-untyped]
except ImportError:
    psycopg = None  # type: ignore[assignment]

SCHEMA_SQL = (Path(__file__).resolve().parent.parent / "sql" / "schema.sql").read_text(
    encoding="utf-8"
)


class SerenDBStore:
    """Best-effort Postgres persistence layer for SerenDB."""

    def __init__(self, dsn: str | None) -> None:
        self.dsn = (dsn or "").strip()
        self.conn: Any = None

    @property
    def enabled(self) -> bool:
        return bool(self.dsn) and psycopg is not None

    def connect(self) -> None:
        if not self.enabled:
            return
        if self.conn is None:
            self.conn = psycopg.connect(self.dsn)

    def ensure_schema(self) -> bool:
        if not self.enabled:
            return False
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        self.conn.commit()
        return True

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def insert_order(
        self,
        *,
        order_id: str,
        run_id: str,
        epoch: int,
        ticker: str,
        side: int,
        size: int,
        price: int,
        status: str = "submitted",
        dry_run: bool = True,
    ) -> None:
        if not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO auction_orders
                   (order_id, run_id, epoch, ticker, side, size, price, status, dry_run)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (order_id) DO UPDATE SET
                       status = EXCLUDED.status,
                       updated_at = NOW()""",
                (order_id, run_id, epoch, ticker, side, size, price, status, dry_run),
            )
        self.conn.commit()

    def update_order_status(self, order_id: str, status: str) -> None:
        if not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE auction_orders SET status = %s, updated_at = NOW() WHERE order_id = %s",
                (status, order_id),
            )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Fills
    # ------------------------------------------------------------------

    def insert_fill(
        self,
        *,
        order_id: str,
        aggressive_id: str,
        passive_id: str,
        price: int,
        qty: int,
        aggressive_side: int,
    ) -> None:
        if not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO auction_fills
                   (order_id, aggressive_id, passive_id, price, qty, aggressive_side)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (order_id, aggressive_id, passive_id, price, qty, aggressive_side),
            )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Market snapshots
    # ------------------------------------------------------------------

    def insert_market_snapshot(
        self,
        *,
        run_id: str,
        ticker: str,
        epoch: int,
        bid: int | None = None,
        ask: int | None = None,
        last_price: int | None = None,
        last_size: int | None = None,
        depth: list[dict] | None = None,
    ) -> None:
        if not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO market_snapshots
                   (run_id, ticker, epoch, bid, ask, last_price, last_size, depth_json)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    run_id,
                    ticker,
                    epoch,
                    bid,
                    ask,
                    last_price,
                    last_size,
                    json.dumps(depth) if depth else None,
                ),
            )
        self.conn.commit()

    # ------------------------------------------------------------------
    # Position snapshots
    # ------------------------------------------------------------------

    def insert_position_snapshot(
        self,
        *,
        run_id: str,
        trader_id: str,
        ticker: str,
        position_side: int,
        avg_price: int,
        open_orders: int = 0,
    ) -> None:
        if not self.enabled:
            return
        self.connect()
        assert self.conn is not None
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO position_snapshots
                   (run_id, trader_id, ticker, position_side, avg_price, open_orders)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (run_id, trader_id, ticker, position_side, avg_price, open_orders),
            )
        self.conn.commit()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
