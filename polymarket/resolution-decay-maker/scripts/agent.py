#!/usr/bin/env python3
"""Resolution-decay maker scaffold for Polymarket binary markets."""

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
    "This strategy can lose money. Backtests are hypothetical, include model "
    "assumptions, and do not guarantee future performance. Use dry-run first, "
    "size conservatively, and only trade with risk capital."
)


@dataclass(frozen=True)
class StrategyParams:
    bankroll_usd: float = 1000.0
    markets_max: int = 8
    min_seconds_to_resolution: int = 45 * 60
    min_edge_bps: float = 2.0
    maker_rebate_bps: float = 2.5
    expected_unwind_cost_bps: float = 1.2
    adverse_selection_bps: float = 1.1
    min_spread_bps: float = 18.0
    max_spread_bps: float = 140.0
    volatility_spread_multiplier: float = 0.35
    decay_alpha_bps: float = 8.0
    decay_horizon_seconds: int = 7 * 24 * 60 * 60
    base_order_notional_usd: float = 22.0
    max_notional_per_market_usd: float = 120.0
    max_total_notional_usd: float = 480.0
    max_position_notional_usd: float = 180.0
    inventory_skew_strength_bps: float = 20.0


@dataclass(frozen=True)
class BacktestParams:
    days: int = 180
    days_min: int = 60
    days_max: int = 365
    participation_rate: float = 0.24
    volatility_window_points: int = 16
    min_history_points: int = 100


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run resolution-decay maker strategy.")
    parser.add_argument("--config", default="config.json", help="Config file path.")
    parser.add_argument(
        "--run-type",
        default="backtest",
        choices=("backtest", "trade"),
        help="Run backtest only, or run trade mode after backtest gating.",
    )
    parser.add_argument(
        "--markets-file",
        default=None,
        help="Optional path to market snapshot JSON for trade mode.",
    )
    parser.add_argument(
        "--backtest-file",
        default=None,
        help="Optional path to historical market JSON for backtest mode.",
    )
    parser.add_argument(
        "--backtest-days",
        type=int,
        default=None,
        help="Override backtest lookback window in days.",
    )
    parser.add_argument(
        "--allow-negative-backtest",
        action="store_true",
        help="Allow trade mode even if backtest return is <= 0.",
    )
    parser.add_argument(
        "--yes-live",
        action="store_true",
        help="Explicit live execution confirmation flag.",
    )
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
        markets_max=max(1, _safe_int(raw.get("markets_max"), 8)),
        min_seconds_to_resolution=max(
            60,
            _safe_int(raw.get("min_seconds_to_resolution"), 45 * 60),
        ),
        min_edge_bps=_safe_float(raw.get("min_edge_bps"), 2.0),
        maker_rebate_bps=_safe_float(raw.get("maker_rebate_bps"), 2.5),
        expected_unwind_cost_bps=_safe_float(raw.get("expected_unwind_cost_bps"), 1.2),
        adverse_selection_bps=_safe_float(raw.get("adverse_selection_bps"), 1.1),
        min_spread_bps=_safe_float(raw.get("min_spread_bps"), 18.0),
        max_spread_bps=_safe_float(raw.get("max_spread_bps"), 140.0),
        volatility_spread_multiplier=_safe_float(
            raw.get("volatility_spread_multiplier"),
            0.35,
        ),
        decay_alpha_bps=_safe_float(raw.get("decay_alpha_bps"), 8.0),
        decay_horizon_seconds=max(
            3600,
            _safe_int(raw.get("decay_horizon_seconds"), 7 * 24 * 60 * 60),
        ),
        base_order_notional_usd=max(1.0, _safe_float(raw.get("base_order_notional_usd"), 22.0)),
        max_notional_per_market_usd=max(
            1.0,
            _safe_float(raw.get("max_notional_per_market_usd"), 120.0),
        ),
        max_total_notional_usd=max(
            1.0,
            _safe_float(raw.get("max_total_notional_usd"), 480.0),
        ),
        max_position_notional_usd=max(
            1.0,
            _safe_float(raw.get("max_position_notional_usd"), 180.0),
        ),
        inventory_skew_strength_bps=max(
            0.0,
            _safe_float(raw.get("inventory_skew_strength_bps"), 20.0),
        ),
    )


def to_backtest_params(config: dict[str, Any]) -> BacktestParams:
    raw = config.get("backtest", {})
    range_raw = raw.get("days_range", {}) if isinstance(raw.get("days_range"), dict) else {}
    days_min = max(7, _safe_int(range_raw.get("min"), 60))
    days_max = max(days_min, _safe_int(range_raw.get("max"), 365))
    days = clamp(_safe_int(raw.get("days"), 180), days_min, days_max)
    return BacktestParams(
        days=int(days),
        days_min=days_min,
        days_max=days_max,
        participation_rate=clamp(_safe_float(raw.get("participation_rate"), 0.24), 0.0, 1.0),
        volatility_window_points=max(4, _safe_int(raw.get("volatility_window_points"), 16)),
        min_history_points=max(8, _safe_int(raw.get("min_history_points"), 48)),
    )


def _normalize_history(raw_history: Any, start_ts: int, end_ts: int) -> list[tuple[int, float]]:
    points: list[tuple[int, float]] = []
    seen: set[int] = set()
    fallback_points: list[tuple[int, float]] = []
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
        if t in fallback_seen:
            continue
        if not (0.0 <= p <= 1.0):
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
        raw_markets = payload.get("markets", [])
    elif isinstance(payload, list):
        raw_markets = payload
    else:
        raw_markets = []

    out: list[dict[str, Any]] = []
    for row in raw_markets:
        if not isinstance(row, dict):
            continue
        history = _normalize_history(row.get("history"), start_ts=start_ts, end_ts=end_ts)
        if len(history) < 2:
            continue
        market_id = _safe_str(row.get("market_id"), "unknown")
        out.append(
            {
                "market_id": market_id,
                "question": _safe_str(row.get("question"), market_id),
                "end_ts": _safe_int(row.get("end_ts"), end_ts + 86400),
                "rebate_bps": _safe_float(row.get("rebate_bps"), 0.0),
                "history": history,
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


def _spread_bps(volatility_bps: float, ttl_ratio: float, p: StrategyParams) -> float:
    decay_tightening = (1.0 - ttl_ratio) * 8.0
    spread = p.min_spread_bps + (volatility_bps * p.volatility_spread_multiplier) - decay_tightening
    return clamp(spread, p.min_spread_bps, p.max_spread_bps)


def _expected_edge_bps(spread_bps: float, ttl_ratio: float, rebate_bps: float, p: StrategyParams) -> float:
    decay_bonus = p.decay_alpha_bps * (1.0 - ttl_ratio)
    return (
        (spread_bps / 2.0)
        + rebate_bps
        + decay_bonus
        - p.expected_unwind_cost_bps
        - p.adverse_selection_bps
    )


def _simulate_market(market: dict[str, Any], p: StrategyParams, bt: BacktestParams) -> dict[str, Any]:
    history = market["history"]
    window = bt.volatility_window_points
    if len(history) < max(bt.min_history_points, window + 2):
        return {
            "market_id": market["market_id"],
            "question": market["question"],
            "quoted_points": 0,
            "considered_points": 0,
            "filled_notional_usd": 0.0,
            "pnl_usd": 0.0,
            "event_pnls": [],
        }

    moves_bps = [abs((history[i][1] - history[i - 1][1]) * 10000.0) for i in range(1, len(history))]
    rebate_bps = _safe_float(market.get("rebate_bps"), p.maker_rebate_bps)
    if rebate_bps <= 0:
        rebate_bps = p.maker_rebate_bps

    considered = 0
    quoted = 0
    filled_notional = 0.0
    pnl = 0.0
    event_pnls: list[float] = []

    for idx in range(window, len(history) - 1):
        t, mid = history[idx]
        _, nxt = history[idx + 1]
        end_ts = _safe_int(market.get("end_ts"), t + p.decay_horizon_seconds)
        ttl = max(0, end_ts - t)
        ttl_ratio = clamp(ttl / p.decay_horizon_seconds, 0.0, 1.0)
        if ttl < p.min_seconds_to_resolution:
            continue
        if mid <= 0.01 or mid >= 0.99:
            continue

        considered += 1
        vol_bps = pstdev(moves_bps[idx - window : idx]) if window > 1 else p.min_spread_bps
        spread_bps = _spread_bps(vol_bps, ttl_ratio, p)
        expected_edge = _expected_edge_bps(spread_bps, ttl_ratio, rebate_bps, p)
        if expected_edge < p.min_edge_bps:
            continue

        quoted += 1
        half_spread_bps = spread_bps / 2.0
        next_move_bps = abs((nxt - mid) * 10000.0)
        touch_ratio = min(1.0, next_move_bps / max(half_spread_bps, 1e-9))
        participation = bt.participation_rate * (1.0 + ((1.0 - ttl_ratio) * 0.25))
        event_notional = p.base_order_notional_usd * participation * touch_ratio
        pickoff_penalty = max(0.0, next_move_bps - half_spread_bps)
        realized_edge = expected_edge - pickoff_penalty
        event_pnl = event_notional * realized_edge / 10000.0
        filled_notional += event_notional
        pnl += event_pnl
        event_pnls.append(event_pnl)

    return {
        "market_id": market["market_id"],
        "question": market["question"],
        "quoted_points": quoted,
        "considered_points": considered,
        "filled_notional_usd": round(filled_notional, 4),
        "pnl_usd": round(pnl, 6),
        "event_pnls": event_pnls,
    }


def run_backtest(config: dict[str, Any], backtest_file: str | None, backtest_days: int | None) -> dict[str, Any]:
    p = to_strategy_params(config)
    bt = to_backtest_params(config)
    requested_days = backtest_days if backtest_days is not None else bt.days
    days = int(clamp(requested_days, bt.days_min, bt.days_max))
    end_ts = int(time.time())
    start_ts = end_ts - (days * 24 * 60 * 60)

    markets = _load_backtest_markets(config, backtest_file, start_ts, end_ts)
    if not markets:
        return {
            "status": "error",
            "error_code": "no_backtest_markets",
            "message": "No historical backtest markets were available.",
            "disclaimer": DISCLAIMER,
            "dry_run": True,
        }

    summaries: list[dict[str, Any]] = []
    event_pnls: list[float] = []
    considered = 0
    quoted = 0
    total_notional = 0.0

    for market in markets[: p.markets_max]:
        result = _simulate_market(market, p, bt)
        summaries.append(
            {
                "market_id": result["market_id"],
                "question": result["question"],
                "considered_points": result["considered_points"],
                "quoted_points": result["quoted_points"],
                "filled_notional_usd": result["filled_notional_usd"],
                "pnl_usd": result["pnl_usd"],
            }
        )
        considered += int(result["considered_points"])
        quoted += int(result["quoted_points"])
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
        "skill": "resolution-decay-maker",
        "mode": "backtest",
        "dry_run": True,
        "backtest_summary": {
            "days": days,
            "days_range": {"min": bt.days_min, "max": bt.days_max},
            "start_utc": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
            "end_utc": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
            "markets_selected": len(summaries),
            "considered_points": considered,
            "quoted_points": quoted,
            "quote_rate_pct": round((quoted / considered) * 100.0 if considered else 0.0, 4),
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
        "markets": sorted(summaries, key=lambda row: row["pnl_usd"], reverse=True),
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


def _quote_market(
    market: dict[str, Any],
    inventory_notional: float,
    outstanding_notional: float,
    p: StrategyParams,
) -> dict[str, Any]:
    market_id = _safe_str(market.get("market_id"), "unknown")
    mid = _safe_float(market.get("mid_price"), 0.5)
    if not (0.01 < mid < 0.99):
        return {"market_id": market_id, "status": "skipped", "reason": "extreme_probability"}

    ttl = max(0, _safe_int(market.get("seconds_to_resolution"), 0))
    if ttl < p.min_seconds_to_resolution:
        return {"market_id": market_id, "status": "skipped", "reason": "near_resolution"}

    vol_bps = max(0.0, _safe_float(market.get("volatility_bps"), p.min_spread_bps))
    ttl_ratio = clamp(ttl / p.decay_horizon_seconds, 0.0, 1.0)
    rebate_bps = _safe_float(market.get("rebate_bps"), p.maker_rebate_bps)
    spread_bps = _spread_bps(vol_bps, ttl_ratio, p)
    edge_bps = _expected_edge_bps(spread_bps, ttl_ratio, rebate_bps, p)
    if edge_bps < p.min_edge_bps:
        return {
            "market_id": market_id,
            "status": "skipped",
            "reason": "negative_or_thin_edge",
            "edge_bps": round(edge_bps, 3),
        }

    inventory_ratio = clamp(inventory_notional / p.max_position_notional_usd, -1.0, 1.0)
    skew_bps = -inventory_ratio * p.inventory_skew_strength_bps
    half_spread_prob = (spread_bps / 2.0) / 10000.0
    skew_prob = skew_bps / 10000.0

    bid_price = clamp(mid - half_spread_prob + skew_prob, 0.001, 0.999)
    ask_price = clamp(mid + half_spread_prob + skew_prob, 0.001, 0.999)
    if bid_price >= ask_price:
        return {"market_id": market_id, "status": "skipped", "reason": "crossed_quote_after_skew"}

    decay_size_bonus = 1.0 + ((1.0 - ttl_ratio) * 0.25)
    target_notional = p.base_order_notional_usd * decay_size_bonus
    remaining_market = max(0.0, p.max_notional_per_market_usd - abs(inventory_notional))
    remaining_total = max(0.0, p.max_total_notional_usd - max(outstanding_notional, 0.0))
    quote_notional = min(target_notional, remaining_market, remaining_total)

    if quote_notional <= 0:
        return {"market_id": market_id, "status": "skipped", "reason": "risk_capacity_exhausted"}

    return {
        "market_id": market_id,
        "status": "quoted",
        "edge_bps": round(edge_bps, 3),
        "spread_bps": round(spread_bps, 3),
        "quote_notional_usd": round(quote_notional, 2),
        "bid_price": round(bid_price, 4),
        "ask_price": round(ask_price, 4),
        "inventory_notional_usd": round(inventory_notional, 2),
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
    markets = _load_trade_markets(config, markets_file)
    inventory_map = config.get("state", {}).get("inventory", {})
    inventory = {str(k): _safe_float(v, 0.0) for k, v in inventory_map.items()}

    quotes: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    outstanding_notional = 0.0

    for market in markets:
        if len(quotes) >= p.markets_max:
            break
        market_id = _safe_str(market.get("market_id"), "unknown")
        proposal = _quote_market(
            market=market,
            inventory_notional=inventory.get(market_id, 0.0),
            outstanding_notional=outstanding_notional,
            p=p,
        )
        if proposal.get("status") == "quoted":
            outstanding_notional += float(proposal["quote_notional_usd"])
            quotes.append(proposal)
        else:
            skips.append(
                {
                    "market_id": market_id,
                    "reason": _safe_str(proposal.get("reason"), "unknown"),
                    "edge_bps": proposal.get("edge_bps"),
                }
            )

    mode = "live" if live_mode and yes_live and not dry_run else "dry-run"
    return {
        "status": "ok",
        "skill": "resolution-decay-maker",
        "mode": mode,
        "dry_run": mode != "live",
        "strategy_summary": {
            "markets_considered": len(markets),
            "markets_quoted": len(quotes),
            "markets_skipped": len(skips),
            "outstanding_notional_usd": round(outstanding_notional, 2),
        },
        "quotes": quotes,
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
        "skill": "resolution-decay-maker",
        "run_type": "trade",
        "backtest": backtest,
        "trade": trade,
        "disclaimer": DISCLAIMER,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
