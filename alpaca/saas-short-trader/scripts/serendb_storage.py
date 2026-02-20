#!/usr/bin/env python3
"""
SerenDB persistence helpers for SaaS short strategy bot.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row


class SerenDBStorage:
    def __init__(self, dsn: str):
        self.dsn = dsn

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

    def check_overlap(self, mode: str, run_type: str, window_hours: int = 6) -> Optional[str]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT run_id
                    FROM trading.strategy_runs
                    WHERE strategy_name = 'saas-short-trader'
                      AND mode = %s
                      AND status = 'running'
                      AND COALESCE(metadata->>'run_type', '') = %s
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
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO trading.strategy_runs
                      (run_id, strategy_name, mode, run_date, status, universe, max_names_scored, max_names_orders, min_conviction, metadata)
                    VALUES
                      (%s, 'saas-short-trader', %s, CURRENT_DATE, %s, %s::text[], %s, %s, %s, %s::jsonb)
                    """,
                    (
                        run_id,
                        mode,
                        status,
                        universe,
                        max_names_scored,
                        max_names_orders,
                        min_conviction,
                        json.dumps(metadata),
                    ),
                )
            conn.commit()
        return run_id

    def update_run_status(self, run_id: str, status: str, metadata_patch: Dict[str, Any]) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE trading.strategy_runs
                    SET status = %s,
                        metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                    WHERE run_id = %s
                    """,
                    (status, json.dumps(metadata_patch), run_id),
                )
            conn.commit()

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
        with self.connect() as conn:
            with conn.cursor() as cur:
                for e in events:
                    cur.execute(
                        """
                        INSERT INTO trading.order_events
                          (run_id, mode, order_ref, broker, ticker, side, order_type, status,
                           qty, limit_price, stop_price, filled_qty, filled_avg_price, is_simulated, details)
                        VALUES
                          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (run_id, order_ref, event_time) DO NOTHING
                        """,
                        (
                            run_id,
                            mode,
                            e["order_ref"],
                            e.get("broker", "alpaca"),
                            e["ticker"],
                            e.get("side", "SELL"),
                            e.get("order_type", "limit"),
                            e.get("status", "planned"),
                            e["qty"],
                            e.get("limit_price"),
                            e.get("stop_price"),
                            e.get("filled_qty"),
                            e.get("filled_avg_price"),
                            bool(e.get("is_simulated", True)),
                            json.dumps(e.get("details", {})),
                        ),
                    )
            conn.commit()

    def upsert_position_marks(self, as_of_date: date, mode: str, rows: List[Dict[str, Any]], source_run_id: str) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                for r in rows:
                    cur.execute(
                        """
                        INSERT INTO trading.position_marks_daily
                          (as_of_date, mode, ticker, qty, avg_entry_price, mark_price, market_value,
                           realized_pnl, unrealized_pnl, gross_exposure, net_exposure, source_run_id)
                        VALUES
                          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (as_of_date, mode, ticker) DO UPDATE
                        SET qty = EXCLUDED.qty,
                            avg_entry_price = EXCLUDED.avg_entry_price,
                            mark_price = EXCLUDED.mark_price,
                            market_value = EXCLUDED.market_value,
                            realized_pnl = EXCLUDED.realized_pnl,
                            unrealized_pnl = EXCLUDED.unrealized_pnl,
                            gross_exposure = EXCLUDED.gross_exposure,
                            net_exposure = EXCLUDED.net_exposure,
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
                            source_run_id,
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
    ) -> None:
        net_pnl = realized_pnl + unrealized_pnl
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO trading.pnl_daily
                      (as_of_date, mode, realized_pnl, unrealized_pnl, net_pnl, gross_exposure, net_exposure,
                       hit_rate, max_drawdown, source_run_id)
                    VALUES
                      (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (as_of_date, mode) DO UPDATE
                    SET realized_pnl = EXCLUDED.realized_pnl,
                        unrealized_pnl = EXCLUDED.unrealized_pnl,
                        net_pnl = EXCLUDED.net_pnl,
                        gross_exposure = EXCLUDED.gross_exposure,
                        net_exposure = EXCLUDED.net_exposure,
                        hit_rate = EXCLUDED.hit_rate,
                        max_drawdown = EXCLUDED.max_drawdown,
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
                        source_run_id,
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
                      e.ticker,
                      e.qty,
                      e.status,
                      e.details
                    FROM trading.order_events e
                    JOIN trading.strategy_runs sr
                      ON sr.run_id = e.run_id
                    WHERE sr.strategy_name = 'saas-short-trader'
                      AND sr.mode = %s
                      AND COALESCE(sr.metadata->>'run_type', '') = 'scan'
                      AND sr.status = 'completed'
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
