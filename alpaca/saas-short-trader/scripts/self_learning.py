#!/usr/bin/env python3
"""
Self-learning runner for saas-short-trader.

This script runs a controlled champion/challenger loop against SerenDB.

Actions:
- label-update: persist feature snapshots + outcome labels
- retrain: train challenger policy from labels
- promotion-check: compare challenger vs champion and promote if gates pass
- full: run label-update -> retrain -> promotion-check

Usage:
  python3 self_learning.py --dsn "$SERENDB_DSN" --action full
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

import psycopg


DEFAULT_WEIGHTS = {"f": 0.30, "a": 0.30, "s": 0.20, "t": 0.20, "p": 1.00}
DEFAULT_THRESHOLDS = {
    "min_conviction": 65.0,
    "max_names_orders": 8,
    "min_liquidity_bucket": "adv_lt_20m",
}
PROMOTION_GATES = {
    "min_trades": 40,
    "min_days": 60,
    "min_net_pnl_improvement_pct": 5.0,
    "min_hit_rate_improvement_pp": 2.0,
    "max_drawdown_deterioration_pct": 10.0,
    "min_horizon_wins": 2,
}


@dataclass
class PolicyMetrics:
    n_trades: int
    n_days: int
    net_pnl: float
    hit_rate: float
    max_drawdown: float
    by_horizon: Dict[str, Dict[str, float]]

    def as_json(self) -> Dict[str, object]:
        return {
            "n_trades": self.n_trades,
            "n_days": self.n_days,
            "net_pnl": round(self.net_pnl, 6),
            "hit_rate": round(self.hit_rate, 6),
            "max_drawdown": round(self.max_drawdown, 6),
            "by_horizon": self.by_horizon,
        }


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def make_policy_version(prefix: str = "v") -> str:
    ts = now_utc().strftime("%Y%m%d%H%M%S")
    return f"{prefix}{ts}"


def safe_float(value: Optional[object], default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def score_from_weights(f: float, a: float, s: float, t: float, p: float, w: Dict[str, float]) -> float:
    raw = (w["f"] * f) + (w["a"] * a) + (w["s"] * s) + (w["t"] * t) + (w["p"] * p)
    score = max(0.0, min(100.0, 20.0 * raw))
    return score


def normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    norm_keys = ["f", "a", "s", "t"]
    total = sum(abs(weights[k]) for k in norm_keys)
    if total <= 0:
        base = {**DEFAULT_WEIGHTS}
        return base
    out = {k: weights[k] / total for k in norm_keys}
    out["p"] = weights.get("p", 1.0)
    return out


def ensure_champion(conn: psycopg.Connection) -> str:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT policy_version
            FROM trading.learning_policy_versions
            WHERE status = 'champion'
            ORDER BY COALESCE(promoted_at, created_at) DESC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row:
            return str(row[0])

        cur.execute(
            """
            INSERT INTO trading.learning_policy_versions
              (policy_version, status, objective, weights, thresholds, metrics, notes)
            VALUES
              (%s, 'champion', 'maximize_risk_adjusted_pnl', %s::jsonb, %s::jsonb, %s::jsonb, %s)
            """,
            (
                "v1.0.0",
                json.dumps(DEFAULT_WEIGHTS),
                json.dumps(DEFAULT_THRESHOLDS),
                json.dumps(
                    {
                        "n_trades": 0,
                        "n_days": 0,
                        "net_pnl": 0.0,
                        "hit_rate": 0.0,
                        "max_drawdown": 0.0,
                        "by_horizon": {},
                    }
                ),
                "Bootstrap champion policy",
            ),
        )
    conn.commit()
    return "v1.0.0"


def log_learning_event(
    conn: psycopg.Connection,
    event_type: str,
    status: str,
    policy_version: Optional[str],
    details: Dict[str, object],
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO trading.learning_events (event_time, event_type, status, policy_version, details)
            VALUES (NOW(), %s, %s, %s, %s::jsonb)
            """,
            (event_type, status, policy_version, json.dumps(details)),
        )
    conn.commit()


def upsert_feature_snapshots(conn: psycopg.Connection, mode: str = "paper-sim") -> int:
    """
    Persist deterministic feature snapshots from candidate_scores for selected names.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH src AS (
              SELECT
                cs.run_id,
                sr.mode,
                COALESCE(sr.metadata->>'run_type', 'scan') AS run_type,
                cs.ticker,
                COALESCE(cs.created_at, NOW()) AS as_of_ts,
                jsonb_build_object(
                  'f', cs.f,
                  'a', cs.a,
                  's', cs.s,
                  't', cs.t,
                  'p', cs.p,
                  'conviction_0_100', cs.conviction_0_100
                ) AS feature_vector,
                jsonb_build_object(
                  'selected', cs.selected,
                  'rank_no', cs.rank_no,
                  'latest_filing_type', cs.latest_filing_type
                ) AS decision
              FROM trading.candidate_scores cs
              JOIN trading.strategy_runs sr ON sr.run_id = cs.run_id
              WHERE sr.mode = %s
            )
            INSERT INTO trading.learning_feature_snapshots
              (run_id, mode, run_type, ticker, as_of_ts, policy_version, feature_vector, decision)
            SELECT
              run_id,
              mode,
              run_type,
              ticker,
              as_of_ts,
              'v1.0.0',
              feature_vector,
              decision
            FROM src
            ON CONFLICT (run_id, ticker) DO UPDATE
              SET mode = EXCLUDED.mode,
                  run_type = EXCLUDED.run_type,
                  as_of_ts = EXCLUDED.as_of_ts,
                  feature_vector = EXCLUDED.feature_vector,
                  decision = EXCLUDED.decision
            """
            ,
            (mode,),
        )
        inserted = cur.rowcount if cur.rowcount is not None else 0
    conn.commit()
    return inserted


def upsert_outcome_labels(conn: psycopg.Connection, mode: str = "paper-sim") -> int:
    """
    Create simple horizon labels using latest mark-to-market from position_marks_daily.

    If per-horizon realized outcomes are unavailable, this falls back to a shared mark-to-market
    label with horizon-specific metadata.
    """
    total = 0
    horizons = ("5D", "10D", "20D")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              fs.run_id,
              fs.ticker,
              COALESCE(pm.as_of_date, CURRENT_DATE) AS label_date,
              COALESCE(pm.unrealized_pnl + pm.realized_pnl, 0) AS realized_pnl,
              CASE
                WHEN COALESCE(pm.avg_entry_price, 0) > 0
                     AND COALESCE(pm.qty, 0) <> 0
                THEN COALESCE((pm.unrealized_pnl + pm.realized_pnl) / NULLIF(ABS(pm.avg_entry_price * pm.qty), 0), 0)
                ELSE 0
              END AS realized_return
            FROM trading.learning_feature_snapshots fs
            LEFT JOIN trading.position_marks_daily pm
              ON pm.source_run_id = fs.run_id
             AND pm.ticker = fs.ticker
             AND pm.mode = fs.mode
            WHERE fs.mode = %s
            """,
            (mode,),
        )
        rows = cur.fetchall()

    with conn.cursor() as cur:
        for run_id, ticker, label_date, realized_pnl, realized_return in rows:
            rpnl = safe_float(realized_pnl)
            rret = safe_float(realized_return)
            beat_hurdle = rret > 0.0
            for horizon in horizons:
                cur.execute(
                    """
                    INSERT INTO trading.learning_outcome_labels
                      (run_id, ticker, horizon, label_date, realized_return, realized_pnl, beat_hurdle, stop_hit, target_hit, metadata)
                    VALUES
                      (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (run_id, ticker, horizon) DO UPDATE
                      SET label_date = EXCLUDED.label_date,
                          realized_return = EXCLUDED.realized_return,
                          realized_pnl = EXCLUDED.realized_pnl,
                          beat_hurdle = EXCLUDED.beat_hurdle,
                          metadata = EXCLUDED.metadata
                    """,
                    (
                        run_id,
                        ticker,
                        horizon,
                        label_date,
                        rret,
                        rpnl,
                        beat_hurdle,
                        False,
                        False,
                        json.dumps({"label_source": "fallback_mark_to_market", "horizon": horizon}),
                    ),
                )
                total += 1
    conn.commit()
    return total


def load_training_rows(conn: psycopg.Connection) -> List[Dict[str, object]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            """
            SELECT
              fs.run_id,
              fs.ticker,
              fs.feature_vector,
              lb.horizon,
              lb.label_date,
              lb.realized_return,
              lb.realized_pnl,
              lb.beat_hurdle
            FROM trading.learning_feature_snapshots fs
            JOIN trading.learning_outcome_labels lb
              ON lb.run_id = fs.run_id
             AND lb.ticker = fs.ticker
            """
        )
        return list(cur.fetchall())


def compute_candidate_weights(rows: List[Dict[str, object]]) -> Dict[str, float]:
    """
    Very simple deterministic learner:
    - compute feature mean difference between positive and non-positive outcomes
    - normalize to get next weights
    """
    winners = []
    losers = []
    for r in rows:
        fv = r["feature_vector"] or {}
        feat = {
            "f": safe_float(fv.get("f")),
            "a": safe_float(fv.get("a")),
            "s": safe_float(fv.get("s")),
            "t": safe_float(fv.get("t")),
            "p": safe_float(fv.get("p")),
        }
        if bool(r.get("beat_hurdle")):
            winners.append(feat)
        else:
            losers.append(feat)

    if not winners or not losers:
        return {**DEFAULT_WEIGHTS}

    def mean(items: List[Dict[str, float]], key: str) -> float:
        return sum(x[key] for x in items) / max(1, len(items))

    raw = {}
    for key in ("f", "a", "s", "t"):
        raw[key] = max(0.01, mean(winners, key) - mean(losers, key))
    raw["p"] = 1.0
    return normalize_weights(raw)


def compute_metrics(rows: List[Dict[str, object]], weights: Dict[str, float], threshold: float) -> PolicyMetrics:
    """
    Evaluate policy by selecting rows above threshold.
    """
    selected = []
    by_h = {"5D": [], "10D": [], "20D": []}

    for r in rows:
        fv = r["feature_vector"] or {}
        score = score_from_weights(
            safe_float(fv.get("f")),
            safe_float(fv.get("a")),
            safe_float(fv.get("s")),
            safe_float(fv.get("t")),
            safe_float(fv.get("p")),
            weights,
        )
        if score < threshold:
            continue

        pnl = safe_float(r.get("realized_pnl"))
        horizon = str(r.get("horizon"))
        label_date = r.get("label_date")
        selected.append((label_date, pnl))
        if horizon in by_h:
            by_h[horizon].append(pnl)

    if not selected:
        return PolicyMetrics(
            n_trades=0,
            n_days=0,
            net_pnl=0.0,
            hit_rate=0.0,
            max_drawdown=0.0,
            by_horizon={h: {"net_pnl": 0.0, "hit_rate": 0.0} for h in ("5D", "10D", "20D")},
        )

    net_pnl = sum(p for _, p in selected)
    wins = sum(1 for _, p in selected if p > 0)
    hit_rate = wins / len(selected)

    # Drawdown on cumulative PnL curve.
    selected_sorted = sorted(selected, key=lambda x: (x[0] or date.today()))
    curve = 0.0
    peak = 0.0
    max_dd = 0.0
    seen_days = set()
    for d, pnl in selected_sorted:
        seen_days.add(d)
        curve += pnl
        peak = max(peak, curve)
        dd = peak - curve
        max_dd = max(max_dd, dd)

    by_horizon = {}
    for h, vals in by_h.items():
        if vals:
            by_horizon[h] = {
                "net_pnl": round(sum(vals), 6),
                "hit_rate": round(sum(1 for v in vals if v > 0) / len(vals), 6),
            }
        else:
            by_horizon[h] = {"net_pnl": 0.0, "hit_rate": 0.0}

    return PolicyMetrics(
        n_trades=len(selected),
        n_days=len(seen_days),
        net_pnl=net_pnl,
        hit_rate=hit_rate,
        max_drawdown=max_dd,
        by_horizon=by_horizon,
    )


def get_policy(conn: psycopg.Connection, status: str) -> Optional[Tuple[str, Dict[str, float], Dict[str, object]]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            """
            SELECT policy_version, weights, metrics
            FROM trading.learning_policy_versions
            WHERE status = %s
            ORDER BY COALESCE(promoted_at, created_at) DESC
            LIMIT 1
            """,
            (status,),
        )
        row = cur.fetchone()
        if not row:
            return None
        weights = row["weights"] or {}
        metrics = row["metrics"] or {}
        return str(row["policy_version"]), weights, metrics


def upsert_policy_assignments(
    conn: psycopg.Connection,
    policy_version: str,
    assignment_type: str,
    mode: Optional[str] = None,
) -> int:
    if assignment_type not in {"champion", "challenger", "shadow"}:
        raise ValueError(f"Unsupported assignment_type: {assignment_type}")

    with conn.cursor() as cur:
        if mode:
            cur.execute(
                """
                INSERT INTO trading.learning_policy_assignments
                  (run_id, policy_version, assignment_type)
                SELECT DISTINCT
                  fs.run_id,
                  %s,
                  %s
                FROM trading.learning_feature_snapshots fs
                WHERE fs.mode = %s
                ON CONFLICT (run_id, assignment_type) DO UPDATE
                SET policy_version = EXCLUDED.policy_version,
                    created_at = NOW()
                """,
                (policy_version, assignment_type, mode),
            )
        else:
            cur.execute(
                """
                INSERT INTO trading.learning_policy_assignments
                  (run_id, policy_version, assignment_type)
                SELECT DISTINCT
                  fs.run_id,
                  %s,
                  %s
                FROM trading.learning_feature_snapshots fs
                ON CONFLICT (run_id, assignment_type) DO UPDATE
                SET policy_version = EXCLUDED.policy_version,
                    created_at = NOW()
                """,
                (policy_version, assignment_type),
            )
        count = cur.rowcount if cur.rowcount is not None else 0
    conn.commit()
    return count


def insert_challenger(
    conn: psycopg.Connection,
    weights: Dict[str, float],
    metrics: PolicyMetrics,
    training_window: Tuple[Optional[date], Optional[date]],
) -> str:
    version = make_policy_version()
    start, end = training_window
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO trading.learning_policy_versions
              (policy_version, status, objective, weights, thresholds, training_window_start, training_window_end, metrics, notes)
            VALUES
              (%s, 'challenger', 'maximize_risk_adjusted_pnl', %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, %s)
            """,
            (
                version,
                json.dumps(weights),
                json.dumps(DEFAULT_THRESHOLDS),
                start,
                end,
                json.dumps(metrics.as_json()),
                "Auto-trained challenger",
            ),
        )
    conn.commit()
    return version


def training_window(rows: List[Dict[str, object]]) -> Tuple[Optional[date], Optional[date]]:
    dates = [r.get("label_date") for r in rows if r.get("label_date") is not None]
    if not dates:
        return None, None
    return min(dates), max(dates)


def pct_improvement(new: float, base: float) -> float:
    if math.isclose(base, 0.0, abs_tol=1e-12):
        return 100.0 if new > 0 else 0.0
    return ((new - base) / abs(base)) * 100.0


def evaluate_promotion(
    champion_metrics: PolicyMetrics,
    challenger_metrics: PolicyMetrics,
) -> Tuple[bool, Dict[str, object]]:
    gates = {}
    gates["min_trades"] = challenger_metrics.n_trades >= PROMOTION_GATES["min_trades"]
    gates["min_days"] = challenger_metrics.n_days >= PROMOTION_GATES["min_days"]

    pnl_impr = pct_improvement(challenger_metrics.net_pnl, champion_metrics.net_pnl)
    gates["net_pnl_improvement"] = pnl_impr >= PROMOTION_GATES["min_net_pnl_improvement_pct"]

    hit_impr_pp = (challenger_metrics.hit_rate - champion_metrics.hit_rate) * 100.0
    gates["hit_rate_improvement"] = hit_impr_pp >= PROMOTION_GATES["min_hit_rate_improvement_pp"]

    if math.isclose(champion_metrics.max_drawdown, 0.0, abs_tol=1e-12):
        dd_deterioration = 0.0 if challenger_metrics.max_drawdown <= 0 else 100.0
    else:
        dd_deterioration = pct_improvement(challenger_metrics.max_drawdown, champion_metrics.max_drawdown)
    gates["drawdown_safety"] = dd_deterioration <= PROMOTION_GATES["max_drawdown_deterioration_pct"]

    horizon_wins = 0
    for h in ("5D", "10D", "20D"):
        c = safe_float(challenger_metrics.by_horizon.get(h, {}).get("net_pnl"))
        b = safe_float(champion_metrics.by_horizon.get(h, {}).get("net_pnl"))
        if c > b:
            horizon_wins += 1
    gates["horizon_stability"] = horizon_wins >= PROMOTION_GATES["min_horizon_wins"]

    pass_all = all(gates.values())
    details = {
        "pass_all": pass_all,
        "gates": gates,
        "net_pnl_improvement_pct": round(pnl_impr, 6),
        "hit_rate_improvement_pp": round(hit_impr_pp, 6),
        "drawdown_deterioration_pct": round(dd_deterioration, 6),
        "horizon_wins": horizon_wins,
    }
    return pass_all, details


def metrics_from_json(payload: Dict[str, object]) -> PolicyMetrics:
    by_h = payload.get("by_horizon") or {}
    return PolicyMetrics(
        n_trades=int(payload.get("n_trades") or 0),
        n_days=int(payload.get("n_days") or 0),
        net_pnl=safe_float(payload.get("net_pnl")),
        hit_rate=safe_float(payload.get("hit_rate")),
        max_drawdown=safe_float(payload.get("max_drawdown")),
        by_horizon={
            "5D": {
                "net_pnl": safe_float((by_h.get("5D") or {}).get("net_pnl")),
                "hit_rate": safe_float((by_h.get("5D") or {}).get("hit_rate")),
            },
            "10D": {
                "net_pnl": safe_float((by_h.get("10D") or {}).get("net_pnl")),
                "hit_rate": safe_float((by_h.get("10D") or {}).get("hit_rate")),
            },
            "20D": {
                "net_pnl": safe_float((by_h.get("20D") or {}).get("net_pnl")),
                "hit_rate": safe_float((by_h.get("20D") or {}).get("hit_rate")),
            },
        },
    )


def promote_challenger(conn: psycopg.Connection, challenger_version: str) -> None:
    with conn.cursor() as cur:
        # Retire current champion.
        cur.execute(
            """
            UPDATE trading.learning_policy_versions
            SET status = 'retired', retired_at = NOW()
            WHERE status = 'champion'
            """
        )
        # Promote challenger.
        cur.execute(
            """
            UPDATE trading.learning_policy_versions
            SET status = 'champion', promoted_at = NOW()
            WHERE policy_version = %s
            """,
            (challenger_version,),
        )
    conn.commit()


def run_label_update(conn: psycopg.Connection, mode: str) -> Dict[str, object]:
    event_details = {"mode": mode}
    log_learning_event(conn, "retrain", "started", None, {"stage": "label-update", **event_details})

    champion = get_policy(conn, "champion")
    champion_version = champion[0] if champion else ensure_champion(conn)
    snapshots = upsert_feature_snapshots(conn, mode=mode)
    labels = upsert_outcome_labels(conn, mode=mode)
    assignments = upsert_policy_assignments(
        conn=conn,
        policy_version=champion_version,
        assignment_type="champion",
        mode=mode,
    )

    details = {
        "feature_rows_upserted": snapshots,
        "label_rows_upserted": labels,
        "policy_assignments_upserted": assignments,
        "champion_policy_version": champion_version,
        **event_details,
    }
    log_learning_event(conn, "retrain", "completed", None, {"stage": "label-update", **details})
    return details


def run_retrain(conn: psycopg.Connection) -> Dict[str, object]:
    log_learning_event(conn, "retrain", "started", None, {"stage": "retrain"})
    rows = load_training_rows(conn)
    if not rows:
        details = {"message": "No training rows available."}
        log_learning_event(conn, "retrain", "blocked", None, details)
        return {"status": "blocked", **details}

    weights = compute_candidate_weights(rows)
    metrics = compute_metrics(rows, weights=weights, threshold=DEFAULT_THRESHOLDS["min_conviction"])
    start, end = training_window(rows)
    version = insert_challenger(conn, weights, metrics, (start, end))
    assignments = upsert_policy_assignments(
        conn=conn,
        policy_version=version,
        assignment_type="challenger",
    )

    details = {
        "status": "completed",
        "challenger_version": version,
        "challenger_assignments_upserted": assignments,
        "training_window_start": str(start) if start else None,
        "training_window_end": str(end) if end else None,
        "weights": weights,
        "metrics": metrics.as_json(),
    }
    log_learning_event(conn, "retrain", "completed", version, details)
    return details


def run_promotion_check(conn: psycopg.Connection) -> Dict[str, object]:
    log_learning_event(conn, "promote", "started", None, {"stage": "promotion-check"})

    champion = get_policy(conn, "champion")
    challenger = get_policy(conn, "challenger")
    if not champion or not challenger:
        details = {"status": "blocked", "message": "Champion or challenger policy missing."}
        log_learning_event(conn, "promote", "blocked", None, details)
        return details

    champion_version, _champ_w, champion_metrics_json = champion
    challenger_version, _chal_w, challenger_metrics_json = challenger
    champion_metrics = metrics_from_json(champion_metrics_json)
    challenger_metrics = metrics_from_json(challenger_metrics_json)

    pass_all, gate_details = evaluate_promotion(champion_metrics, challenger_metrics)
    if pass_all:
        promote_challenger(conn, challenger_version)
        champion_assignments = upsert_policy_assignments(
            conn=conn,
            policy_version=challenger_version,
            assignment_type="champion",
        )
        details = {
            "status": "promoted",
            "champion_before": champion_version,
            "champion_after": challenger_version,
            "champion_assignments_upserted": champion_assignments,
            **gate_details,
        }
        log_learning_event(conn, "promote", "completed", challenger_version, details)
        return details

    details = {
        "status": "blocked",
        "champion_before": champion_version,
        "champion_after": champion_version,
        "challenger_version": challenger_version,
        **gate_details,
    }
    log_learning_event(conn, "promote", "blocked", challenger_version, details)
    return details


def run_full(conn: psycopg.Connection, mode: str) -> Dict[str, object]:
    label = run_label_update(conn, mode=mode)
    retrain = run_retrain(conn)
    promotion = run_promotion_check(conn)
    return {"label_update": label, "retrain": retrain, "promotion_check": promotion}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Self-learning runner for saas-short-trader")
    parser.add_argument("--dsn", required=True, help="Postgres DSN for user's SerenDB database")
    parser.add_argument(
        "--action",
        required=True,
        choices=["label-update", "retrain", "promotion-check", "full"],
        help="Action to run",
    )
    parser.add_argument(
        "--mode",
        default="paper-sim",
        choices=["paper", "paper-sim", "live"],
        help="Mode for label updates",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with psycopg.connect(args.dsn) as conn:
        ensure_champion(conn)

        if args.action == "label-update":
            out = run_label_update(conn, mode=args.mode)
        elif args.action == "retrain":
            out = run_retrain(conn)
        elif args.action == "promotion-check":
            out = run_promotion_check(conn)
        else:
            out = run_full(conn, mode=args.mode)

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
