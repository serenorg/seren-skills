#!/usr/bin/env python3
"""
SerenDB persistence helpers for SaaS short strategy bot.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from trade_reporting import ShortTradeReportEmitter


SKILL_SLUG = "alpaca-sass-short-trader-delta-neutral"
STRATEGY_NAME = "sass-short-trader-delta-neutral"


class SerenDBStorage:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.reporter = ShortTradeReportEmitter(
            skill_slug=SKILL_SLUG,
            strategy_name=STRATEGY_NAME,
        )

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def apply_sql_file(self, sql_file: Path) -> None:
        sql_text = sql_file.read_text(encoding="utf-8")
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql_text)
            conn.commit()

    def ensure_schemas(self, base_sql: Path, learning_sql: Path) -> None:
        self.apply_sql_file(base_sql)
        self.apply_sql_file(learning_sql)

    @staticmethod
    def _period_bounds(as_of_date: date) -> Tuple[str, str]:
        start = datetime.combine(as_of_date, datetime.min.time(), tzinfo=timezone.utc)
        end = datetime.combine(as_of_date, datetime.max.time(), tzinfo=timezone.utc)
        return start.isoformat(), end.isoformat()

    @staticmethod
    def _position_side(net_exposure: Any) -> Optional[str]:
        try:
            value = float(net_exposure)
        except (TypeError, ValueError):
            return None
        if value < 0:
            return "SELL"
        if value > 0:
            return "BUY"
        return None

    def check_overlap(self, mode: str, run_type: str, window_hours: int = 6) -> Optional[str]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT run_id
                    FROM trading.strategy_runs
                    WHERE strategy_name = 'sass-short-trader-delta-neutral'
                      AND mode = %s
                      AND status = 'running'
                      AND COALESCE(run_type, metadata->>'run_type', '') = %s
                      AND created_at >= NOW() - (%s || ' hours')::interval
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (mode, run_type, window_hours),
                )
                row = cur.fetchone()
                return str(row["run_id"]) if row else None

    def insert_run(
        self,
        mode: str,
        universe: List[str],
        max_names_scored: int,
        max_names_orders: int,
        min_conviction: float,
        status: str,
        metadata: Dict[str, Any],
    ) -> str:
        run_id = str(uuid4())
        run_type = str(metadata.get("run_type") or "").strip() or None
        self.reporter.start_run(
            run_id,
            mode=mode,
            dry_run=mode != "live",
            metadata=metadata,
        )
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO trading.strategy_runs
                      (run_id, skill_slug, venue, strategy_name, mode, run_type, status, dry_run, started_at,
                       run_date, universe, max_names_scored, max_names_orders, min_conviction, config, summary, metadata)
                    VALUES
                      (%s, %s, 'alpaca', %s, %s, %s, %s, %s, NOW(),
                       CURRENT_DATE, %s::text[], %s, %s, %s, %s::jsonb, '{}'::jsonb, %s::jsonb)
                    """,
                    (
                        run_id,
                        SKILL_SLUG,
                        STRATEGY_NAME,
                        mode,
                        run_type,
                        status,
                        mode != "live",
                        universe,
                        max_names_scored,
                        max_names_orders,
                        min_conviction,
                        json.dumps(
                            {
                                "universe": universe,
                                "max_names_scored": max_names_scored,
                                "max_names_orders": max_names_orders,
                                "min_conviction": min_conviction,
                            }
                        ),
                        json.dumps(metadata),
                    ),
                )
            conn.commit()
        return run_id

    def update_run_status(self, run_id: str, status: str, metadata_patch: Dict[str, Any]) -> None:
        error_message = str(metadata_patch.get("error") or "").strip() or None
        error_code = status if error_message else None
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE trading.strategy_runs
                    SET status = %s,
                        completed_at = CASE
                            WHEN %s IN ('completed', 'failed', 'blocked', 'stopped')
                            THEN COALESCE(completed_at, NOW())
                            ELSE completed_at
                        END,
                        summary = COALESCE(summary, '{}'::jsonb) || %s::jsonb,
                        metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb,
                        error_code = COALESCE(%s, error_code),
                        error_message = COALESCE(%s, error_message)
                    WHERE run_id = %s
                    """,
                    (
                        status,
                        status,
                        json.dumps(metadata_patch),
                        json.dumps(metadata_patch),
                        error_code,
                        error_message,
                        run_id,
                    ),
                )
            conn.commit()
        if status in {"completed", "failed", "blocked", "stopped"}:
            self.reporter.finish_run(run_id, status=status, metadata_patch=metadata_patch)

    def insert_candidate_scores(self, run_id: str, rows: List[Dict[str, Any]]) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                for r in rows:
                    cur.execute(
                        """
                        INSERT INTO trading.candidate_scores
                          (run_id, ticker, rank_no, selected, f, a, s, t, p, conviction_0_100,
                           latest_filing_date, latest_filing_type, evidence_sec, evidence_news, evidence_trends,
                           catalyst_type, catalyst_date, catalyst_bias, catalyst_confidence, catalyst_note)
                        VALUES
                          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                           %s, %s, %s, %s, %s)
                        ON CONFLICT (run_id, ticker) DO UPDATE
                        SET rank_no = EXCLUDED.rank_no,
                            selected = EXCLUDED.selected,
                            f = EXCLUDED.f,
                            a = EXCLUDED.a,
                            s = EXCLUDED.s,
                            t = EXCLUDED.t,
                            p = EXCLUDED.p,
                            conviction_0_100 = EXCLUDED.conviction_0_100,
                            latest_filing_date = EXCLUDED.latest_filing_date,
                            latest_filing_type = EXCLUDED.latest_filing_type,
                            evidence_sec = EXCLUDED.evidence_sec,
                            evidence_news = EXCLUDED.evidence_news,
                            evidence_trends = EXCLUDED.evidence_trends,
                            catalyst_type = EXCLUDED.catalyst_type,
                            catalyst_date = EXCLUDED.catalyst_date,
                            catalyst_bias = EXCLUDED.catalyst_bias,
                            catalyst_confidence = EXCLUDED.catalyst_confidence,
                            catalyst_note = EXCLUDED.catalyst_note
                        """,
                        (
                            run_id,
                            r["ticker"],
                            r["rank_no"],
                            r["selected"],
                            r["f"],
                            r["a"],
                            r["s"],
                            r["t"],
                            r["p"],
                            r["conviction_0_100"],
                            r.get("latest_filing_date"),
                            r.get("latest_filing_type"),
                            json.dumps(r.get("evidence_sec", {})),
                            json.dumps(r.get("evidence_news", {})),
                            json.dumps(r.get("evidence_trends", {})),
                            r.get("catalyst_type"),
                            r.get("catalyst_date"),
                            r.get("catalyst_bias"),
                            r.get("catalyst_confidence"),
                            r.get("catalyst_note"),
                        ),
                    )
            conn.commit()

    def insert_order_events(self, run_id: str, mode: str, events: List[Dict[str, Any]]) -> None:
        self.reporter.record_order_events(run_id, events)
        with self.connect() as conn:
            with conn.cursor() as cur:
                for e in events:
                    details = e.get("details", {})
                    quantity = e["qty"]
                    price = e.get("limit_price") or details.get("entry_price")
                    notional = details.get("planned_notional_usd")
                    if notional is None and price is not None:
                        try:
                            notional = float(price) * float(quantity)
                        except (TypeError, ValueError):
                            notional = None
                    cur.execute(
                        """
                        INSERT INTO trading.order_events
                          (run_id, mode, order_ref, order_id, instrument_id, symbol, broker, ticker, side, order_type,
                           event_type, status, qty, quantity, price, limit_price, stop_price,
                           filled_qty, filled_avg_price, notional_usd, is_simulated, details, metadata)
                        VALUES
                          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                        ON CONFLICT (run_id, order_ref, event_time) DO NOTHING
                        """,
                        (
                            run_id,
                            mode,
                            e["order_ref"],
                            e.get("order_id", e["order_ref"]),
                            e.get("instrument_id", e["ticker"]),
                            e.get("symbol", e["ticker"]),
                            e.get("broker", "alpaca"),
                            e["ticker"],
                            e.get("side", "SELL"),
                            e.get("order_type", "limit"),
                            e.get("status", "planned"),
                            e.get("status", "planned"),
                            e["qty"],
                            quantity,
                            price,
                            e.get("limit_price"),
                            e.get("stop_price"),
                            e.get("filled_qty"),
                            e.get("filled_avg_price"),
                            notional,
                            bool(e.get("is_simulated", True)),
                            json.dumps(details),
                            json.dumps(details),
                        ),
                    )
                    filled_qty = e.get("filled_qty")
                    filled_price = e.get("filled_avg_price")
                    if filled_qty is not None and filled_price is not None:
                        realized_pnl = details.get("realized_pnl")
                        cur.execute(
                            """
                            INSERT INTO trading.fills
                              (run_id, order_id, instrument_id, symbol, side, fill_price, fill_quantity,
                               notional_usd, realized_pnl_usd, metadata)
                            VALUES
                              (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                            """,
                            (
                                run_id,
                                e.get("order_id", e["order_ref"]),
                                e.get("instrument_id", e["ticker"]),
                                e.get("symbol", e["ticker"]),
                                e.get("side", "SELL"),
                                filled_price,
                                filled_qty,
                                float(filled_qty) * float(filled_price),
                                realized_pnl,
                                json.dumps(details),
                            ),
                        )
            conn.commit()

    def upsert_position_marks(
        self,
        as_of_date: date,
        mode: str,
        rows: List[Dict[str, Any]],
        source_run_id: str,
        scan_run_id: Optional[str] = None,
    ) -> None:
        self.reporter.record_position_marks(source_run_id, rows)
        with self.connect() as conn:
            with conn.cursor() as cur:
                for r in rows:
                    side = self._position_side(r.get("net_exposure"))
                    status = "closed" if abs(float(r["qty"])) <= 1e-9 else "open"
                    row_scan_run_id = str(r.get("scan_run_id") or scan_run_id or source_run_id)
                    period_start, _ = self._period_bounds(as_of_date)
                    metadata_json = json.dumps(
                        {
                            "as_of_date": as_of_date.isoformat(),
                            "mode": mode,
                            "gross_exposure": r.get("gross_exposure"),
                            "net_exposure": r.get("net_exposure"),
                            "scan_run_id": row_scan_run_id,
                        }
                    )
                    cur.execute(
                        """
                        INSERT INTO trading.position_marks_daily
                          (as_of_date, mode, ticker, qty, avg_entry_price, mark_price, market_value,
                           realized_pnl, unrealized_pnl, gross_exposure, net_exposure, scan_run_id, source_run_id)
                        VALUES
                          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (as_of_date, mode, ticker) DO UPDATE
                        SET qty = EXCLUDED.qty,
                            avg_entry_price = EXCLUDED.avg_entry_price,
                            mark_price = EXCLUDED.mark_price,
                            market_value = EXCLUDED.market_value,
                            realized_pnl = EXCLUDED.realized_pnl,
                            unrealized_pnl = EXCLUDED.unrealized_pnl,
                            gross_exposure = EXCLUDED.gross_exposure,
                            net_exposure = EXCLUDED.net_exposure,
                            scan_run_id = EXCLUDED.scan_run_id,
                            source_run_id = EXCLUDED.source_run_id
                        """,
                        (
                            as_of_date,
                            mode,
                            r["ticker"],
                            r["qty"],
                            r["avg_entry_price"],
                            r["mark_price"],
                            r["market_value"],
                            r.get("realized_pnl", 0.0),
                            r.get("unrealized_pnl", 0.0),
                            r.get("gross_exposure"),
                            r.get("net_exposure"),
                            row_scan_run_id,
                            source_run_id,
                        ),
                    )
                    cur.execute(
                        """
                        INSERT INTO trading.positions
                          (run_id, position_key, instrument_id, symbol, side, quantity, entry_price,
                           cost_basis_usd, market_price, market_value_usd, unrealized_pnl_usd,
                           realized_pnl_usd, status, opened_at, closed_at, metadata)
                        VALUES
                          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (run_id, position_key) DO UPDATE
                        SET instrument_id = EXCLUDED.instrument_id,
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
                            opened_at = COALESCE(trading.positions.opened_at, EXCLUDED.opened_at),
                            closed_at = EXCLUDED.closed_at,
                            metadata = EXCLUDED.metadata
                        """,
                        (
                            source_run_id,
                            r["ticker"],
                            r["ticker"],
                            r["ticker"],
                            side,
                            r["qty"],
                            r["avg_entry_price"],
                            abs(float(r["avg_entry_price"]) * float(r["qty"])),
                            r["mark_price"],
                            r["market_value"],
                            r.get("unrealized_pnl", 0.0),
                            r.get("realized_pnl", 0.0),
                            status,
                            period_start,
                            period_start if status == "closed" else None,
                            metadata_json,
                        ),
                    )
                    cur.execute(
                        """
                        INSERT INTO trading.position_marks
                          (run_id, position_key, instrument_id, symbol, side, quantity,
                           mark_price, market_value_usd, unrealized_pnl_usd, realized_pnl_usd, mark_time, metadata)
                        VALUES
                          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            source_run_id,
                            r["ticker"],
                            r["ticker"],
                            r["ticker"],
                            side,
                            r["qty"],
                            r["mark_price"],
                            r["market_value"],
                            r.get("unrealized_pnl", 0.0),
                            r.get("realized_pnl", 0.0),
                            period_start,
                            metadata_json,
                        ),
                    )
            conn.commit()

    def upsert_pnl_daily(
        self,
        as_of_date: date,
        mode: str,
        realized_pnl: float,
        unrealized_pnl: float,
        gross_exposure: float,
        net_exposure: float,
        hit_rate: float,
        max_drawdown: float,
        source_run_id: str,
        scan_run_id: Optional[str] = None,
    ) -> None:
        self.reporter.record_pnl(
            source_run_id,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            gross_exposure=gross_exposure,
            net_exposure=net_exposure,
            hit_rate=hit_rate,
            max_drawdown=max_drawdown,
        )
        net_pnl = realized_pnl + unrealized_pnl
        period_start, period_end = self._period_bounds(as_of_date)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO trading.pnl_daily
                      (as_of_date, mode, realized_pnl, unrealized_pnl, net_pnl, gross_exposure, net_exposure,
                       hit_rate, max_drawdown, scan_run_id, source_run_id)
                    VALUES
                      (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (as_of_date, mode) DO UPDATE
                    SET realized_pnl = EXCLUDED.realized_pnl,
                        unrealized_pnl = EXCLUDED.unrealized_pnl,
                        net_pnl = EXCLUDED.net_pnl,
                        gross_exposure = EXCLUDED.gross_exposure,
                        net_exposure = EXCLUDED.net_exposure,
                        hit_rate = EXCLUDED.hit_rate,
                        max_drawdown = EXCLUDED.max_drawdown,
                        scan_run_id = EXCLUDED.scan_run_id,
                        source_run_id = EXCLUDED.source_run_id
                    """,
                    (
                        as_of_date,
                        mode,
                        realized_pnl,
                        unrealized_pnl,
                        net_pnl,
                        gross_exposure,
                        net_exposure,
                        hit_rate,
                        max_drawdown,
                        scan_run_id,
                        source_run_id,
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO trading.pnl_periods
                      (run_id, period_type, period_start, period_end, realized_pnl_usd, unrealized_pnl_usd,
                       gross_pnl_usd, net_pnl_usd, metadata)
                    VALUES
                      (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        source_run_id,
                        "daily",
                        period_start,
                        period_end,
                        realized_pnl,
                        unrealized_pnl,
                        net_pnl,
                        net_pnl,
                        json.dumps(
                            {
                                "mode": mode,
                                "gross_exposure": gross_exposure,
                                "net_exposure": net_exposure,
                                "hit_rate": hit_rate,
                                "max_drawdown": max_drawdown,
                            }
                        ),
                    ),
                )
            conn.commit()

    def get_latest_selected_orders(self, mode: str) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      e.run_id,
                      e.order_ref,
                      e.ticker,
                      e.side,
                      e.qty,
                      e.status,
                      e.details
                    FROM trading.order_events e
                    JOIN trading.strategy_runs sr
                      ON sr.run_id = e.run_id
                    WHERE sr.strategy_name = 'sass-short-trader-delta-neutral'
                      AND sr.mode = %s
                      AND COALESCE(sr.run_type, sr.metadata->>'run_type', '') = 'scan'
                      AND sr.status = 'completed'
                      AND e.side IN ('SELL', 'BUY')
                      AND NOT EXISTS (
                        SELECT 1
                        FROM trading.order_events x
                        WHERE x.mode = e.mode
                          AND x.status IN ('closed_target', 'closed_stop', 'closed_eod', 'closed_manual')
                          AND COALESCE(x.details->>'open_order_ref', '') = e.order_ref
                          AND (
                            (e.side = 'SELL' AND x.side = 'BUY')
                            OR
                            (e.side = 'BUY' AND x.side = 'SELL')
                          )
                      )
                    ORDER BY sr.created_at DESC
                    LIMIT 200
                    """,
                    (mode,),
                )
                rows = cur.fetchall()
        # Keep latest per ticker.
        dedup: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            if r["ticker"] not in dedup:
                dedup[r["ticker"]] = r
        return list(dedup.values())

    def get_pnl_series(self, mode: str) -> List[float]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT net_pnl
                    FROM trading.pnl_daily
                    WHERE mode = %s
                    ORDER BY as_of_date ASC
                    """,
                    (mode,),
                )
                rows = cur.fetchall()
                return [float(r["net_pnl"]) for r in rows]
