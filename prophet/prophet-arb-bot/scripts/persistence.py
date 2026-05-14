"""SerenDB-backed run recorder for the arb-bot.

Persistence is real Postgres: project=`prophet`, database=`prophet`. The
schema lives in `serendb_schema.sql` and is applied during `--command
setup` (idempotent — every CREATE is `IF NOT EXISTS`).

Why direct Postgres and not the seren-db publisher's HTTP API:
  - The `seren-db` publisher provisions databases but does not expose
    an ad-hoc `run-sql` endpoint. SQL execution happens through the
    Postgres connection URI returned by `/projects/{id}/connection_uri`.
  - `scripts/db.py` hides the resolve-URI + open-psycopg2 dance.

Idempotency contract:
  - `arb_runs.run_id` is unique (PRIMARY KEY).
  - `arb_pairs.prophet_market_id` is unique.
  - `arb_orders.prophet_order_id` is unique.
  - `arb_opportunities` has no natural key — re-running a tick re-inserts.
    The cron is hourly so duplicates are bounded; the operator dashboard
    de-duplicates on (run_id, prophet_market_id) for display.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from db import ResolvedTarget, get_target, open_connection


@dataclass
class RunRecorder:
    """Buffers run state in memory; flushes to SerenDB on `finish`.

    A single transaction wraps the run-shell upsert + opportunity inserts
    + order inserts so a partial flush leaves no orphan rows. The
    arb_pairs upsert happens earlier (during setup) and is not part of
    this transaction.
    """

    run_id: str
    target: ResolvedTarget
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str = ""
    status: str = "in_progress"
    summary: dict[str, Any] = field(default_factory=dict)
    pairs: list[dict[str, str]] = field(default_factory=list)
    opportunities: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # In-memory recording — same shape callers used in the prior version

    def record_pair(self, prophet_market_id: str, polymarket_condition_id: str) -> None:
        self.pairs.append(
            {
                "prophet_market_id": prophet_market_id,
                "polymarket_condition_id": polymarket_condition_id,
            }
        )

    def record_opportunity(self, opportunity: Any) -> None:
        self.opportunities.append(asdict(opportunity))

    def record_order(self, order: Any) -> None:
        self.orders.append(asdict(order))

    def attach_hedge_outcome(
        self,
        *,
        prophet_order_id: str,
        hedge_status: str,
        polymarket_order_id: str | None,
        polymarket_filled_qty: float,
        polymarket_fill_price: float,
        error: str | None = None,
    ) -> None:
        """Mutate the recorded order entry with hedge metadata.

        Called from the agent run loop after `hedge_filled_order`
        resolves. If no row matches ``prophet_order_id`` (e.g. dry-run
        synthesized hedge), this is a silent no-op so tests don't have
        to thread fake recorder state through every path.
        """
        for entry in self.orders:
            if entry.get("order_id") == prophet_order_id:
                entry["hedge_status"] = hedge_status
                entry["polymarket_order_id"] = polymarket_order_id
                entry["polymarket_filled_qty"] = polymarket_filled_qty
                entry["polymarket_fill_price"] = polymarket_fill_price
                if error:
                    entry["hedge_error"] = error
                return

    def record_blocker(self, code: str) -> None:
        self.blockers.append(code)

    # ------------------------------------------------------------------
    # Flush

    def finish(self, status: str, reason: str) -> dict[str, Any]:
        self.status = status
        self.finished_at = datetime.now(timezone.utc).isoformat()

        with open_connection(self.target) as conn:
            with conn.cursor() as cur:
                # Upsert the run-shell.
                cur.execute(
                    """
                    INSERT INTO arb_runs (run_id, mode, status, summary, started_at, finished_at)
                    VALUES (%s, 'A', %s, %s::jsonb, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                      status = EXCLUDED.status,
                      summary = EXCLUDED.summary,
                      finished_at = EXCLUDED.finished_at
                    """,
                    (
                        self.run_id,
                        self.status,
                        json.dumps({**self.summary, "reason": reason, "blockers": self.blockers}),
                        self.started_at,
                        self.finished_at,
                    ),
                )
                # Insert opportunities.
                for opp in self.opportunities:
                    cur.execute(
                        """
                        INSERT INTO arb_opportunities (
                          run_id, prophet_market_id, polymarket_condition_id,
                          side, outcome, spread, edge, size_usdc, limit_price,
                          reason, health_warnings
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                        """,
                        (
                            self.run_id,
                            opp.get("prophet_market_id"),
                            opp.get("polymarket_condition_id"),
                            opp.get("side"),
                            opp.get("outcome"),
                            opp.get("spread"),
                            opp.get("edge"),
                            opp.get("size_usdc"),
                            opp.get("limit_price"),
                            opp.get("reason"),
                            json.dumps(opp.get("health_warnings") or []),
                        ),
                    )
                # Upsert orders. Hedge columns are populated only when
                # the runner ran in delta-neutral mode; single-leg rows
                # keep the defaults (`polymarket_filled_qty=0`,
                # `polymarket_fill_price=0`, `polymarket_order_id=NULL`,
                # `hedge_status='pending'`).
                for order in self.orders:
                    cur.execute(
                        """
                        INSERT INTO arb_orders (
                          prophet_order_id, run_id, prophet_market_id,
                          side, outcome, shares, limit_price, status,
                          polymarket_filled_qty, polymarket_fill_price,
                          polymarket_order_id, hedge_status
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (prophet_order_id) DO UPDATE SET
                          status = EXCLUDED.status,
                          polymarket_filled_qty = EXCLUDED.polymarket_filled_qty,
                          polymarket_fill_price = EXCLUDED.polymarket_fill_price,
                          polymarket_order_id = EXCLUDED.polymarket_order_id,
                          hedge_status = EXCLUDED.hedge_status,
                          last_seen_at = NOW()
                        """,
                        (
                            order.get("order_id"),
                            self.run_id,
                            order.get("market_id"),
                            order.get("side"),
                            order.get("outcome"),
                            order.get("shares"),
                            order.get("limit_price"),
                            order.get("status"),
                            order.get("polymarket_filled_qty", 0.0),
                            order.get("polymarket_fill_price", 0.0),
                            order.get("polymarket_order_id"),
                            order.get("hedge_status", "pending"),
                        ),
                    )
            conn.commit()

        return {
            "run_id": self.run_id,
            "status": self.status,
            "reason": reason,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": self.summary,
            "pairs": self.pairs,
            "opportunities": self.opportunities,
            "orders": self.orders,
            "blockers": self.blockers,
        }


# ---------------------------------------------------------------------------
# Schema bootstrap


def apply_schema(target: ResolvedTarget, schema_sql: str) -> None:
    """Apply the schema in one transaction. Idempotent — every statement
    is `CREATE ... IF NOT EXISTS`.
    """
    with open_connection(target) as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()


# ---------------------------------------------------------------------------
# Pair management


def upsert_arb_pair(
    *,
    target: ResolvedTarget,
    prophet_market_id: str,
    polymarket_condition_id: str,
    source_skill: str = "manual",
) -> None:
    with open_connection(target) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO arb_pairs
                  (prophet_market_id, polymarket_condition_id, source_skill)
                VALUES (%s, %s, %s)
                ON CONFLICT (prophet_market_id) DO UPDATE SET
                  polymarket_condition_id = EXCLUDED.polymarket_condition_id,
                  source_skill = EXCLUDED.source_skill,
                  last_seen_at = NOW()
                """,
                (prophet_market_id, polymarket_condition_id, source_skill),
            )
        conn.commit()


def list_arb_pairs(*, target: ResolvedTarget) -> list[dict[str, str]]:
    with open_connection(target) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT prophet_market_id, polymarket_condition_id
                FROM arb_pairs
                ORDER BY last_seen_at DESC
                LIMIT 200
                """
            )
            rows = cur.fetchall()
    return [
        {"prophet_market_id": r[0], "polymarket_condition_id": r[1]}
        for r in rows
        if r[0] and r[1]
    ]


# ---------------------------------------------------------------------------
# Status reads


def list_open_orders(*, target: ResolvedTarget) -> list[dict[str, Any]]:
    with open_connection(target) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT prophet_order_id, prophet_market_id, side, outcome,
                       shares, limit_price, status, last_seen_at
                FROM arb_orders
                WHERE status IN ('open', 'partial')
                ORDER BY last_seen_at DESC
                """
            )
            rows = cur.fetchall()
    return [
        {
            "prophet_order_id": r[0],
            "prophet_market_id": r[1],
            "side": r[2],
            "outcome": r[3],
            "shares": float(r[4]) if r[4] is not None else 0.0,
            "limit_price": float(r[5]) if r[5] is not None else 0.0,
            "status": r[6],
            "last_seen_at": r[7].isoformat() if r[7] else "",
        }
        for r in rows
    ]


def list_recent_runs(
    *, target: ResolvedTarget, limit: int = 10
) -> list[dict[str, Any]]:
    with open_connection(target) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT run_id, status, started_at, finished_at
                FROM arb_runs
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
    return [
        {
            "run_id": r[0],
            "status": r[1],
            "started_at": r[2].isoformat() if r[2] else "",
            "finished_at": r[3].isoformat() if r[3] else "",
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Convenience for tests / external callers


def resolve_for_skill() -> ResolvedTarget:
    """Default resolution for the arb-bot: project=prophet, database=prophet."""
    return get_target(project_name="prophet", database_name="prophet")
