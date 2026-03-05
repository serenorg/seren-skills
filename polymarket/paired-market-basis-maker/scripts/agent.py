#!/usr/bin/env python3
"""Paired-market basis maker scaffold for Polymarket binary markets."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
from typing import Any


DISCLAIMER = (
    "This strategy can lose money. Pair relationships can break, basis can widen, "
    "and liquidity can vanish. Backtests are hypothetical and do not guarantee future "
    "performance. Use dry-run first and only trade with risk capital."
)


@dataclass(frozen=True)
class StrategyParams:
    bankroll_usd: float = 1000.0
    pairs_max: int = 6
    min_seconds_to_resolution: int = 2 * 60 * 60
    min_edge_bps: float = 2.0
    maker_rebate_bps: float = 2.3
    expected_unwind_cost_bps: float = 1.5
    adverse_selection_bps: float = 1.1
    basis_entry_bps: float = 35.0
    basis_exit_bps: float = 10.0
    expected_convergence_ratio: float = 0.35
    base_pair_notional_usd: float = 28.0
    max_notional_per_pair_usd: float = 140.0
    max_total_notional_usd: float = 560.0
    max_leg_notional_usd: float = 220.0


@dataclass(frozen=True)
class BacktestParams:
    days: int = 270
    days_min: int = 90
    days_max: int = 540
    participation_rate: float = 0.26
    min_history_points: int = 36


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paired-market basis maker strategy.")
    parser.add_argument("--config", default="config.json", help="Config file path.")
    parser.add_argument(
        "--run-type",
        default="backtest",
        choices=("backtest", "trade"),
        help="Run backtest only, or run trade mode after backtest gating.",
    )
    parser.add_argument("--markets-file", default=None, help="Optional trade market JSON file.")
    parser.add_argument("--backtest-file", default=None, help="Optional backtest market JSON file.")
    parser.add_argument("--backtest-days", type=int, default=None, help="Override backtest days.")
    parser.add_argument(
        "--allow-negative-backtest",
        action="store_true",
        help="Allow trade mode even if backtest return is <= 0.",
    )
    parser.add_argument("--yes-live", action="store_true", help="Explicit live execution confirmation.")
    return parser.parse_args()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def load_json(path: Path) -> dict[str, Any] | list[Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(config_path: str) -> dict[str, Any]:
    payload = load_json(Path(config_path))
    return payload if isinstance(payload, dict) else {}


def to_strategy_params(config: dict[str, Any]) -> StrategyParams:
    raw = config.get("strategy", {})
    return StrategyParams(
        bankroll_usd=max(1.0, _safe_float(raw.get("bankroll_usd"), 1000.0)),
        pairs_max=max(1, _safe_int(raw.get("pairs_max"), 6)),
        min_seconds_to_resolution=max(60, _safe_int(raw.get("min_seconds_to_resolution"), 7200)),
        min_edge_bps=_safe_float(raw.get("min_edge_bps"), 2.0),
        maker_rebate_bps=_safe_float(raw.get("maker_rebate_bps"), 2.3),
        expected_unwind_cost_bps=_safe_float(raw.get("expected_unwind_cost_bps"), 1.5),
        adverse_selection_bps=_safe_float(raw.get("adverse_selection_bps"), 1.1),
        basis_entry_bps=max(1.0, _safe_float(raw.get("basis_entry_bps"), 35.0)),
        basis_exit_bps=max(0.0, _safe_float(raw.get("basis_exit_bps"), 10.0)),
        expected_convergence_ratio=clamp(
            _safe_float(raw.get("expected_convergence_ratio"), 0.35),
            0.0,
            1.0,
        ),
        base_pair_notional_usd=max(1.0, _safe_float(raw.get("base_pair_notional_usd"), 28.0)),
        max_notional_per_pair_usd=max(1.0, _safe_float(raw.get("max_notional_per_pair_usd"), 140.0)),
        max_total_notional_usd=max(1.0, _safe_float(raw.get("max_total_notional_usd"), 560.0)),
        max_leg_notional_usd=max(1.0, _safe_float(raw.get("max_leg_notional_usd"), 220.0)),
    )


def to_backtest_params(config: dict[str, Any]) -> BacktestParams:
    raw = config.get("backtest", {})
    range_raw = raw.get("days_range", {}) if isinstance(raw.get("days_range"), dict) else {}
    days_min = max(7, _safe_int(range_raw.get("min"), 90))
    days_max = max(days_min, _safe_int(range_raw.get("max"), 540))
    days = int(clamp(_safe_int(raw.get("days"), 270), days_min, days_max))
    return BacktestParams(
        days=days,
        days_min=days_min,
        days_max=days_max,
        participation_rate=clamp(_safe_float(raw.get("participation_rate"), 0.26), 0.0, 1.0),
        min_history_points=max(8, _safe_int(raw.get("min_history_points"), 36)),
    )


def _normalize_history(raw_history: Any, start_ts: int, end_ts: int) -> list[tuple[int, float]]:
    points: list[tuple[int, float]] = []
    fallback_points: list[tuple[int, float]] = []
    seen: set[int] = set()
    fallback_seen: set[int] = set()

    if not isinstance(raw_history, list):
        return points

    for item in raw_history:
        t = -1
        p = -1.0
        if isinstance(item, dict):
            t = _safe_int(item.get("t"), -1)
            p = _safe_float(item.get("p"), -1.0)
        elif isinstance(item, list | tuple) and len(item) >= 2:
            t = _safe_int(item[0], -1)
            p = _safe_float(item[1], -1.0)
        if t in fallback_seen or not (0.0 <= p <= 1.0):
            continue
        fallback_seen.add(t)
        fallback_points.append((t, p))
        if t < start_ts or t > end_ts or t in seen:
            continue
        seen.add(t)
        points.append((t, p))

    points.sort(key=lambda pair: pair[0])
    if points:
        return points
    fallback_points.sort(key=lambda pair: pair[0])
    return fallback_points


def _load_backtest_markets(config: dict[str, Any], backtest_file: str | None, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
    if backtest_file:
        payload = load_json(Path(backtest_file))
    else:
        payload = config.get("backtest_markets", [])

    if isinstance(payload, dict):
        rows = payload.get("markets", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []

    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        primary = _normalize_history(row.get("history"), start_ts=start_ts, end_ts=end_ts)
        pair = _normalize_history(row.get("pair_history"), start_ts=start_ts, end_ts=end_ts)
        aligned_len = min(len(primary), len(pair))
        if aligned_len < 2:
            continue
        market_id = _safe_str(row.get("market_id"), "unknown")
        pair_market_id = _safe_str(row.get("pair_market_id"), f"{market_id}-pair")
        out.append(
            {
                "market_id": market_id,
                "pair_market_id": pair_market_id,
                "question": _safe_str(row.get("question"), market_id),
                "end_ts": _safe_int(row.get("end_ts"), end_ts + 86400),
                "rebate_bps": _safe_float(row.get("rebate_bps"), 0.0),
                "history": primary[:aligned_len],
                "pair_history": pair[:aligned_len],
            }
        )
    return out


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = float("-inf")
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        max_dd = max(max_dd, peak - value)
    return max_dd


def _simulate_pair(market: dict[str, Any], p: StrategyParams, bt: BacktestParams) -> dict[str, Any]:
    primary = market["history"]
    pair = market["pair_history"]
    n = min(len(primary), len(pair))
    if n < bt.min_history_points:
        return {
            "market_id": market["market_id"],
            "pair_market_id": market["pair_market_id"],
            "considered_points": 0,
            "traded_points": 0,
            "filled_notional_usd": 0.0,
            "pnl_usd": 0.0,
            "event_pnls": [],
        }

    rebate_bps = _safe_float(market.get("rebate_bps"), p.maker_rebate_bps)
    if rebate_bps <= 0:
        rebate_bps = p.maker_rebate_bps

    basis_series_bps = [(primary[i][1] - pair[i][1]) * 10000.0 for i in range(n)]
    considered = 0
    traded = 0
    filled_notional = 0.0
    pnl = 0.0
    event_pnls: list[float] = []

    for i in range(0, n - 1):
        t = primary[i][0]
        ttl = max(0, _safe_int(market.get("end_ts"), t + 86400) - t)
        if ttl < p.min_seconds_to_resolution:
            continue

        basis_now = basis_series_bps[i]
        basis_next = basis_series_bps[i + 1]
        abs_basis_now = abs(basis_now)
        if abs_basis_now < p.basis_entry_bps:
            continue

        considered += 1
        basis_change = abs_basis_now - abs(basis_next)
        expected_convergence = abs_basis_now * p.expected_convergence_ratio
        expected_edge = expected_convergence + rebate_bps - p.expected_unwind_cost_bps - p.adverse_selection_bps
        if expected_edge < p.min_edge_bps:
            continue

        traded += 1
        fill_intensity = min(1.0, abs_basis_now / max(p.basis_entry_bps * 2.0, 1e-9))
        event_notional = p.base_pair_notional_usd * bt.participation_rate * fill_intensity
        realized_edge = basis_change + rebate_bps - p.expected_unwind_cost_bps - p.adverse_selection_bps
        event_pnl = event_notional * realized_edge / 10000.0

        filled_notional += event_notional
        pnl += event_pnl
        event_pnls.append(event_pnl)

    return {
        "market_id": market["market_id"],
        "pair_market_id": market["pair_market_id"],
        "considered_points": considered,
        "traded_points": traded,
        "filled_notional_usd": round(filled_notional, 4),
        "pnl_usd": round(pnl, 6),
        "event_pnls": event_pnls,
    }


def run_backtest(config: dict[str, Any], backtest_file: str | None, backtest_days: int | None) -> dict[str, Any]:
    p = to_strategy_params(config)
    bt = to_backtest_params(config)
    days = int(clamp(backtest_days if backtest_days is not None else bt.days, bt.days_min, bt.days_max))

    end_ts = int(time.time())
    start_ts = end_ts - (days * 24 * 60 * 60)

    markets = _load_backtest_markets(config, backtest_file, start_ts, end_ts)
    if not markets:
        return {
            "status": "error",
            "error_code": "no_backtest_markets",
            "message": "No paired historical markets were available for backtest.",
            "disclaimer": DISCLAIMER,
            "dry_run": True,
        }

    summaries: list[dict[str, Any]] = []
    event_pnls: list[float] = []
    considered = 0
    traded = 0
    total_notional = 0.0

    for market in markets[: p.pairs_max]:
        result = _simulate_pair(market, p, bt)
        summaries.append(
            {
                "market_id": result["market_id"],
                "pair_market_id": result["pair_market_id"],
                "considered_points": result["considered_points"],
                "traded_points": result["traded_points"],
                "filled_notional_usd": result["filled_notional_usd"],
                "pnl_usd": result["pnl_usd"],
            }
        )
        considered += int(result["considered_points"])
        traded += int(result["traded_points"])
        total_notional += float(result["filled_notional_usd"])
        event_pnls.extend(result["event_pnls"])

    equity_curve = [p.bankroll_usd]
    equity = p.bankroll_usd
    for event_pnl in event_pnls:
        equity += event_pnl
        equity_curve.append(equity)

    total_pnl = equity - p.bankroll_usd
    return_pct = (total_pnl / p.bankroll_usd) * 100.0

    return {
        "status": "ok",
        "skill": "paired-market-basis-maker",
        "mode": "backtest",
        "dry_run": True,
        "backtest_summary": {
            "days": days,
            "days_range": {"min": bt.days_min, "max": bt.days_max},
            "start_utc": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
            "end_utc": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
            "pairs_selected": len(summaries),
            "considered_points": considered,
            "traded_points": traded,
            "trade_rate_pct": round((traded / considered) * 100.0 if considered else 0.0, 4),
        },
        "results": {
            "starting_bankroll_usd": round(p.bankroll_usd, 2),
            "ending_bankroll_usd": round(equity, 2),
            "total_pnl_usd": round(total_pnl, 4),
            "return_pct": round(return_pct, 4),
            "filled_notional_usd": round(total_notional, 2),
            "events": len(event_pnls),
            "max_drawdown_usd": round(_max_drawdown(equity_curve), 4),
            "decision_hint": "consider_trade_mode" if total_pnl > 0 else "paper_only_or_tune",
        },
        "pairs": sorted(summaries, key=lambda row: row["pnl_usd"], reverse=True),
        "disclaimer": DISCLAIMER,
    }


def _load_trade_markets(config: dict[str, Any], markets_file: str | None) -> list[dict[str, Any]]:
    if markets_file:
        payload = load_json(Path(markets_file))
    else:
        payload = config.get("markets", [])

    if isinstance(payload, dict):
        rows = payload.get("markets", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []

    return [row for row in rows if isinstance(row, dict)]


def _build_pair_trade(market: dict[str, Any], leg_exposure: dict[str, float], total_notional: float, p: StrategyParams) -> dict[str, Any]:
    market_id = _safe_str(market.get("market_id"), "unknown")
    pair_market_id = _safe_str(market.get("pair_market_id"), f"{market_id}-pair")
    mid = _safe_float(market.get("mid_price"), -1.0)
    pair_mid = _safe_float(market.get("pair_mid_price"), -1.0)
    ttl = max(0, _safe_int(market.get("seconds_to_resolution"), 0))

    if ttl < p.min_seconds_to_resolution:
        return {"market_id": market_id, "status": "skipped", "reason": "near_resolution"}
    if not (0.01 < mid < 0.99 and 0.01 < pair_mid < 0.99):
        return {"market_id": market_id, "status": "skipped", "reason": "invalid_mid_prices"}

    basis_bps = (mid - pair_mid) * 10000.0
    abs_basis = abs(basis_bps)
    if abs_basis < p.basis_entry_bps:
        return {
            "market_id": market_id,
            "status": "skipped",
            "reason": "basis_below_entry_threshold",
            "basis_bps": round(basis_bps, 3),
        }

    expected_convergence_bps = abs_basis * p.expected_convergence_ratio
    edge_bps = expected_convergence_bps + p.maker_rebate_bps - p.expected_unwind_cost_bps - p.adverse_selection_bps
    if edge_bps < p.min_edge_bps:
        return {
            "market_id": market_id,
            "status": "skipped",
            "reason": "negative_or_thin_edge",
            "basis_bps": round(basis_bps, 3),
            "edge_bps": round(edge_bps, 3),
        }

    target_notional = p.base_pair_notional_usd * min(1.8, abs_basis / p.basis_entry_bps)
    remaining_total = max(0.0, p.max_total_notional_usd - max(total_notional, 0.0))
    remaining_pair = max(
        0.0,
        p.max_notional_per_pair_usd
        - max(abs(leg_exposure.get(market_id, 0.0)), abs(leg_exposure.get(pair_market_id, 0.0))),
    )
    quote_notional = min(target_notional, remaining_total, remaining_pair)
    if quote_notional <= 0:
        return {"market_id": market_id, "status": "skipped", "reason": "risk_capacity_exhausted"}

    primary_bias = "sell_primary_buy_pair" if basis_bps > 0 else "buy_primary_sell_pair"
    primary_side = "SELL" if basis_bps > 0 else "BUY"
    pair_side = "BUY" if basis_bps > 0 else "SELL"

    return {
        "market_id": market_id,
        "pair_market_id": pair_market_id,
        "status": "quoted",
        "basis_bps": round(basis_bps, 3),
        "expected_convergence_bps": round(expected_convergence_bps, 3),
        "edge_bps": round(edge_bps, 3),
        "trade_bias": primary_bias,
        "pair_notional_usd": round(quote_notional, 2),
        "legs": [
            {"market_id": market_id, "side": primary_side, "notional_usd": round(quote_notional, 2)},
            {"market_id": pair_market_id, "side": pair_side, "notional_usd": round(quote_notional, 2)},
        ],
    }


def run_trade(config: dict[str, Any], markets_file: str | None, yes_live: bool) -> dict[str, Any]:
    execution = config.get("execution", {}) if isinstance(config.get("execution"), dict) else {}
    dry_run = bool(execution.get("dry_run", True))
    live_mode = bool(execution.get("live_mode", False))

    if live_mode and not yes_live:
        return {
            "status": "error",
            "error_code": "live_confirmation_required",
            "message": "Set --yes-live with execution.live_mode=true for live orders.",
            "dry_run": True,
            "disclaimer": DISCLAIMER,
        }
    if live_mode and dry_run:
        return {
            "status": "error",
            "error_code": "invalid_execution_mode",
            "message": "dry_run must be false when live_mode is true.",
            "dry_run": True,
            "disclaimer": DISCLAIMER,
        }

    p = to_strategy_params(config)
    exposure = config.get("state", {}).get("leg_exposure", {})
    leg_exposure = {str(k): _safe_float(v, 0.0) for k, v in exposure.items()}
    markets = _load_trade_markets(config, markets_file)

    trades: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    total_notional = 0.0

    for market in markets:
        if len(trades) >= p.pairs_max:
            break
        market_id = _safe_str(market.get("market_id"), "unknown")
        proposal = _build_pair_trade(
            market=market,
            leg_exposure=leg_exposure,
            total_notional=total_notional,
            p=p,
        )
        if proposal.get("status") == "quoted":
            trades.append(proposal)
            total_notional += float(proposal["pair_notional_usd"])
        else:
            skips.append(
                {
                    "market_id": market_id,
                    "reason": _safe_str(proposal.get("reason"), "unknown"),
                    "basis_bps": proposal.get("basis_bps"),
                    "edge_bps": proposal.get("edge_bps"),
                }
            )

    mode = "live" if live_mode and yes_live and not dry_run else "dry-run"
    return {
        "status": "ok",
        "skill": "paired-market-basis-maker",
        "mode": mode,
        "dry_run": mode != "live",
        "strategy_summary": {
            "pairs_considered": len(markets),
            "pairs_quoted": len(trades),
            "pairs_skipped": len(skips),
            "total_pair_notional_usd": round(total_notional, 2),
            "basis_entry_bps": p.basis_entry_bps,
            "basis_exit_bps": p.basis_exit_bps,
        },
        "pair_trades": trades,
        "skips": skips,
        "disclaimer": DISCLAIMER,
    }


def main() -> int:
    args = parse_args()
    config = load_config(args.config)

    backtest = run_backtest(
        config=config,
        backtest_file=args.backtest_file,
        backtest_days=args.backtest_days,
    )
    if backtest.get("status") != "ok":
        print(json.dumps(backtest, sort_keys=True))
        return 1

    if args.run_type == "backtest":
        print(json.dumps(backtest, sort_keys=True))
        return 0

    execution = config.get("execution", {}) if isinstance(config.get("execution"), dict) else {}
    require_positive = bool(execution.get("require_positive_backtest", True))
    return_pct = _safe_float(backtest.get("results", {}).get("return_pct"), 0.0)
    if require_positive and return_pct <= 0.0 and not args.allow_negative_backtest:
        payload = {
            "status": "error",
            "error_code": "backtest_gate_blocked",
            "message": (
                "Trade mode blocked because backtest return_pct <= 0. "
                "Use --allow-negative-backtest to override."
            ),
            "backtest": backtest,
            "disclaimer": DISCLAIMER,
            "dry_run": True,
        }
        print(json.dumps(payload, sort_keys=True))
        return 1

    trade = run_trade(config=config, markets_file=args.markets_file, yes_live=args.yes_live)
    ok = trade.get("status") == "ok"
    payload = {
        "status": "ok" if ok else "error",
        "skill": "paired-market-basis-maker",
        "run_type": "trade",
        "backtest": backtest,
        "trade": trade,
        "disclaimer": DISCLAIMER,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
