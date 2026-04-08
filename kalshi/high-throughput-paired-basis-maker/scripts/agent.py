#!/usr/bin/env python3
"""Paired-market basis maker for Kalshi event contracts.

Discovers correlated pairs within the same Kalshi event (via /events API),
runs an event-driven backtest with stateful replay, and optionally emits
paired trade intents through the Kalshi REST API.

Prices from Kalshi are in CENTS (1-99). Internally normalized to 0.01-0.99.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from statistics import pstdev
from typing import Any

# --- Force unbuffered stdout so piped/background output is visible immediately ---
if not sys.stdout.isatty():
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
# --- End unbuffered stdout fix ---

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from pair_stateful_replay import (
    PairReplayParams,
    normalize_orderbook_snapshots,
    simulate_pair_backtest,
    write_telemetry_records,
)
from risk_guards import (
    auto_pause_cron,
    check_drawdown_stop_loss,
    check_position_age,
    sync_position_timestamps,
)

DISCLAIMER = (
    "This strategy can lose money. Pair relationships can break, basis can widen, "
    "and liquidity can vanish. Backtests are hypothetical and do not guarantee future "
    "performance. Use dry-run first and only trade with risk capital."
)

KALSHI_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
SKILL_SLUG = "kalshi-high-throughput-paired-basis-maker"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StrategyParams:
    bankroll: float = 1000.0
    pairs_max: int = 10
    basis_entry_bps: float = 35.0
    basis_exit_bps: float = 10.0
    min_edge_bps: float = 2.0
    expected_unwind_cost_bps: float = 2.0
    expected_convergence_ratio: float = 0.35
    base_pair_notional_usd: float = 600.0
    max_notional_per_pair_usd: float = 850.0
    max_total_notional_usd: float = 2000.0
    max_leg_notional_usd: float = 900.0


@dataclass(frozen=True)
class BacktestParams:
    days: int = 270
    days_min: int = 90
    days_max: int = 540
    participation_rate: float = 0.95
    spread_decay_bps: float = 45.0
    min_history_points: int = 72
    min_events: int = 200
    min_liquidity_usd: float = 5000.0
    telemetry_path: str = ""
    bankroll_usd: float = 100.0
    volatility_window_points: int = 24
    synthetic_orderbook_half_spread_bps: float = 18.0
    synthetic_orderbook_depth_usd: float = 125.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _timestamp_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run paired-market basis maker strategy on Kalshi.")
    parser.add_argument("--config", default="config.json", help="Config file path.")
    parser.add_argument(
        "--run-type",
        default="backtest",
        choices=("backtest", "trade"),
        help="Run backtest only, or run trade mode after backtest gating.",
    )
    parser.add_argument(
        "--backtest-file",
        default=None,
        help="Optional paired backtest fixture JSON file with history and orderbook snapshots.",
    )
    parser.add_argument("--backtest-days", type=int, default=None, help="Override backtest days.")
    parser.add_argument("--yes-live", action="store_true", help="Explicit live execution confirmation.")
    parser.add_argument("--unwind-all", action="store_true", help="Emergency: cancel all orders, market-sell all positions.")
    return parser.parse_args()


def _bootstrap_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if path.exists():
        return path
    example_path = path.with_name("config.example.json")
    if example_path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def load_config(config_path: str) -> dict[str, Any]:
    path = _bootstrap_config_path(config_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def to_strategy_params(config: dict[str, Any]) -> StrategyParams:
    raw = config.get("strategy", {})
    if not isinstance(raw, dict):
        raw = {}
    return StrategyParams(
        bankroll=max(1.0, _safe_float(raw.get("bankroll"), 1000.0)),
        pairs_max=max(1, _safe_int(raw.get("pairs_max"), 10)),
        basis_entry_bps=max(1.0, _safe_float(raw.get("basis_entry_bps"), 35.0)),
        basis_exit_bps=max(0.0, _safe_float(raw.get("basis_exit_bps"), 10.0)),
        min_edge_bps=_safe_float(raw.get("min_edge_bps"), 2.0),
        expected_unwind_cost_bps=_safe_float(raw.get("expected_unwind_cost_bps"), 2.0),
        expected_convergence_ratio=clamp(
            _safe_float(raw.get("expected_convergence_ratio"), 0.35), 0.0, 1.0,
        ),
        base_pair_notional_usd=max(1.0, _safe_float(raw.get("base_pair_notional_usd"), 600.0)),
        max_notional_per_pair_usd=max(1.0, _safe_float(raw.get("max_notional_per_pair_usd"), 850.0)),
        max_total_notional_usd=max(1.0, _safe_float(raw.get("max_total_notional_usd"), 2000.0)),
        max_leg_notional_usd=max(1.0, _safe_float(raw.get("max_leg_notional_usd"), 900.0)),
    )


def to_backtest_params(config: dict[str, Any]) -> BacktestParams:
    raw = config.get("backtest", {})
    if not isinstance(raw, dict):
        raw = {}
    days_min = max(7, _safe_int(raw.get("days_min"), 90))
    days_max_val = max(days_min, _safe_int(raw.get("days_max"), 540))
    days = int(clamp(_safe_int(raw.get("days"), 270), days_min, days_max_val))
    return BacktestParams(
        days=days,
        days_min=days_min,
        days_max=days_max_val,
        participation_rate=clamp(_safe_float(raw.get("participation_rate"), 0.95), 0.0, 1.0),
        spread_decay_bps=max(1.0, _safe_float(raw.get("spread_decay_bps"), 45.0)),
        min_history_points=max(8, _safe_int(raw.get("min_history_points"), 72)),
        min_events=max(1, _safe_int(raw.get("min_events"), 200)),
        min_liquidity_usd=max(0.0, _safe_float(raw.get("min_liquidity_usd"), 5000.0)),
        telemetry_path=_safe_str(raw.get("telemetry_path"), ""),
        bankroll_usd=max(1.0, _safe_float(raw.get("bankroll_usd"), 100.0)),
        volatility_window_points=max(3, _safe_int(raw.get("volatility_window_points"), 24)),
        synthetic_orderbook_half_spread_bps=max(
            0.1, _safe_float(raw.get("synthetic_orderbook_half_spread_bps"), 18.0),
        ),
        synthetic_orderbook_depth_usd=max(
            0.0, _safe_float(raw.get("synthetic_orderbook_depth_usd"), 125.0),
        ),
    )


def _to_pair_replay_params(p: StrategyParams, bt: BacktestParams) -> PairReplayParams:
    return PairReplayParams(
        bankroll_usd=bt.bankroll_usd,
        basis_entry_bps=p.basis_entry_bps,
        basis_exit_bps=p.basis_exit_bps,
        min_edge_bps=p.min_edge_bps,
        expected_unwind_cost_bps=p.expected_unwind_cost_bps,
        expected_convergence_ratio=p.expected_convergence_ratio,
        base_pair_notional_usd=p.base_pair_notional_usd,
        max_notional_per_pair_usd=p.max_notional_per_pair_usd,
        max_total_notional_usd=p.max_total_notional_usd,
        max_leg_notional_usd=p.max_leg_notional_usd,
        participation_rate=bt.participation_rate,
        min_history_points=bt.min_history_points,
        volatility_window_points=bt.volatility_window_points,
        spread_decay_bps=bt.spread_decay_bps,
        synthetic_orderbook_half_spread_bps=bt.synthetic_orderbook_half_spread_bps,
        synthetic_orderbook_depth_usd=bt.synthetic_orderbook_depth_usd,
        telemetry_path=bt.telemetry_path,
    )


# ---------------------------------------------------------------------------
# Kalshi API helpers (lightweight, no dependency on kalshi_client for backtest)
# ---------------------------------------------------------------------------

def _kalshi_get_json(path: str, api_key: str = "", timeout: int = 30) -> Any:
    """Unauthenticated GET for public Kalshi endpoints (markets, events)."""
    from urllib.request import Request, urlopen

    url = f"{KALSHI_API_BASE}{path}"
    headers = {"Accept": "application/json"}
    if api_key:
        # For authenticated endpoints, we would need RSA signing.
        # Public market data works without auth on Kalshi.
        headers["Authorization"] = f"Bearer {api_key}"
    req = Request(url, headers=headers, method="GET")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cents_to_decimal(cents: int | float) -> float:
    """Convert Kalshi cents (1-99) to decimal probability (0.01-0.99)."""
    return float(cents) / 100.0


# ---------------------------------------------------------------------------
# Pair discovery via Kalshi Events API
# ---------------------------------------------------------------------------

def _fetch_live_backtest_pairs(
    p: StrategyParams,
    bt: BacktestParams,
    start_ts: int,
    end_ts: int,
) -> list[dict[str, Any]]:
    """Discover Kalshi event-grouped markets and build basis pairs.

    Strategy: Use /events to find events with multiple related markets.
    For each event with 2+ markets, pair them up and check for basis
    dislocations in their price histories.
    """
    pairs: list[dict[str, Any]] = []
    replay_params = _to_pair_replay_params(p, bt)
    cursor = None
    events_fetched = 0
    max_event_pages = 50

    for page in range(max_event_pages):
        try:
            params_str = f"?limit=100&status=open&with_nested_markets=true"
            if cursor:
                params_str += f"&cursor={cursor}"
            response = _kalshi_get_json(f"/events{params_str}")
        except Exception:
            break

        if not isinstance(response, dict):
            break

        events = response.get("events", [])
        if not isinstance(events, list) or not events:
            break

        for event in events:
            if not isinstance(event, dict):
                continue
            markets = event.get("markets", [])
            if not isinstance(markets, list) or len(markets) < 2:
                continue

            event_ticker = _safe_str(event.get("event_ticker"), "")
            events_fetched += 1

            # Filter to open markets with adequate volume
            eligible = []
            for m in markets:
                if not isinstance(m, dict):
                    continue
                status = _safe_str(m.get("status"), "")
                if status not in ("open", "active", ""):
                    continue
                ticker = _safe_str(m.get("ticker"), "")
                if not ticker:
                    continue
                volume = _safe_int(m.get("volume", 0), 0)
                yes_bid = _safe_int(m.get("yes_bid", 0), 0)
                yes_ask = _safe_int(m.get("yes_ask", 0), 0)
                mid_cents = (yes_bid + yes_ask) / 2.0 if yes_bid > 0 and yes_ask > 0 else 50.0
                eligible.append({
                    "ticker": ticker,
                    "title": _safe_str(m.get("title"), ticker),
                    "mid_cents": mid_cents,
                    "volume": volume,
                    "event_ticker": event_ticker,
                })

            if len(eligible) < 2:
                continue

            # Pair all eligible markets within the same event
            for m1, m2 in combinations(eligible, 2):
                pair_market = _build_pair_from_event_markets(
                    m1, m2, replay_params, start_ts, end_ts,
                )
                if pair_market is not None:
                    pairs.append(pair_market)
                    if len(pairs) >= p.pairs_max * 3:
                        return pairs

        cursor = response.get("cursor")
        if not cursor:
            break

    return pairs


def _build_pair_from_event_markets(
    m1: dict[str, Any],
    m2: dict[str, Any],
    replay_params: PairReplayParams,
    start_ts: int,
    end_ts: int,
) -> dict[str, Any] | None:
    """Attempt to build a backtest-ready pair from two Kalshi event markets.

    Fetches price history for both legs and attaches synthetic orderbooks.
    """
    ticker1 = m1["ticker"]
    ticker2 = m2["ticker"]

    try:
        h1_resp = _kalshi_get_json(f"/markets/{ticker1}/history?limit=1000&min_ts={start_ts}&max_ts={end_ts}")
        h2_resp = _kalshi_get_json(f"/markets/{ticker2}/history?limit=1000&min_ts={start_ts}&max_ts={end_ts}")
    except Exception:
        return None

    history1 = _parse_kalshi_history(h1_resp)
    history2 = _parse_kalshi_history(h2_resp)

    if len(history1) < replay_params.min_history_points or len(history2) < replay_params.min_history_points:
        return None

    # Attach synthetic orderbooks
    books1, mode1 = normalize_orderbook_snapshots([], history1, replay_params)
    books2, mode2 = normalize_orderbook_snapshots([], history2, replay_params)

    return {
        "market_id": ticker1,
        "pair_market_id": ticker2,
        "question": m1.get("title", ticker1),
        "pair_question": m2.get("title", ticker2),
        "event_ticker": m1.get("event_ticker", ""),
        "history": history1,
        "pair_history": history2,
        "orderbooks": books1,
        "pair_orderbooks": books2,
        "orderbook_mode": f"{mode1}|{mode2}" if mode1 != mode2 else mode1,
        "end_ts": end_ts,
    }


def _parse_kalshi_history(response: Any) -> list[tuple[int, float]]:
    """Parse Kalshi market history response into (timestamp, decimal_price) tuples."""
    points: list[tuple[int, float]] = []
    if isinstance(response, dict):
        history = response.get("history", response.get("ticks", []))
    elif isinstance(response, list):
        history = response
    else:
        return points

    if not isinstance(history, list):
        return points

    seen: set[int] = set()
    for item in history:
        if isinstance(item, dict):
            ts = _safe_int(item.get("ts", item.get("t", item.get("timestamp", -1))), -1)
            # Kalshi prices in cents
            price_cents = _safe_float(
                item.get("yes_price", item.get("price", item.get("yes_bid", -1))),
                -1.0,
            )
            if ts < 0 or price_cents < 0:
                continue
            price = _cents_to_decimal(price_cents) if price_cents > 1.0 else price_cents
            if ts not in seen and 0.0 <= price <= 1.0:
                seen.add(ts)
                points.append((ts, price))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            ts = _safe_int(item[0], -1)
            price = _safe_float(item[1], -1.0)
            if price > 1.0:
                price = _cents_to_decimal(price)
            if ts >= 0 and 0.0 <= price <= 1.0 and ts not in seen:
                seen.add(ts)
                points.append((ts, price))

    points.sort(key=lambda x: x[0])
    return points


# ---------------------------------------------------------------------------
# Backtest from file
# ---------------------------------------------------------------------------

def _load_backtest_markets(
    p: StrategyParams,
    bt: BacktestParams,
    start_ts: int,
    end_ts: int,
) -> tuple[list[dict[str, Any]], str]:
    """Load backtest markets from live API or return empty for fixture override."""
    markets = _fetch_live_backtest_pairs(p, bt, start_ts, end_ts)
    return markets, "live"


def _load_backtest_from_file(path: str) -> list[dict[str, Any]]:
    """Load a pre-built backtest fixture file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("markets", raw.get("pairs", []))
    return []


# ---------------------------------------------------------------------------
# Single pair simulation wrapper
# ---------------------------------------------------------------------------

def _simulate_pair(
    market: dict[str, Any],
    p: StrategyParams,
    bt: BacktestParams,
    allocated_capital: float = 0.0,
) -> dict[str, Any]:
    """Run pair replay for a single market pair."""
    replay_params = _to_pair_replay_params(p, bt)

    # Ensure orderbooks exist (synthetic if not provided)
    if "orderbooks" not in market or not market["orderbooks"]:
        books, mode = normalize_orderbook_snapshots(
            market.get("raw_orderbooks", []),
            market.get("history", []),
            replay_params,
        )
        market["orderbooks"] = books
        market["orderbook_mode"] = mode
    if "pair_orderbooks" not in market or not market["pair_orderbooks"]:
        books, mode = normalize_orderbook_snapshots(
            market.get("raw_pair_orderbooks", []),
            market.get("pair_history", []),
            replay_params,
        )
        market["pair_orderbooks"] = books
        if "orderbook_mode" not in market:
            market["orderbook_mode"] = mode

    return simulate_pair_backtest(market, replay_params, allocated_capital)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _rank_pair_markets(
    markets: list[dict[str, Any]],
    results: list[dict[str, Any]],
    params: StrategyParams,
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    """Score and rank pair markets by Sharpe-like metric."""
    scored: list[tuple[dict[str, Any], dict[str, Any], float]] = []
    for market, result in zip(markets, results):
        pnl = _safe_float(result.get("pnl_usd"), 0.0)
        event_pnls = result.get("event_pnls", [])
        if not event_pnls:
            scored.append((market, result, 0.0))
            continue
        mean_pnl = sum(event_pnls) / len(event_pnls)
        std_pnl = pstdev(event_pnls) if len(event_pnls) > 1 else abs(mean_pnl) or 1.0
        sharpe = mean_pnl / max(std_pnl, 1e-9)
        scored.append((market, result, sharpe))
    scored.sort(key=lambda x: x[2], reverse=True)
    return scored[:params.pairs_max]


# ---------------------------------------------------------------------------
# Backtest orchestrator
# ---------------------------------------------------------------------------

def run_backtest(
    config: dict[str, Any],
    backtest_days: int | None,
    backtest_file: str | None = None,
) -> dict[str, Any]:
    """Full backtest orchestrator. Returns JSON-serializable result."""
    p = to_strategy_params(config)
    bt = to_backtest_params(config)
    if backtest_days is not None:
        bt = BacktestParams(
            days=int(clamp(backtest_days, bt.days_min, bt.days_max)),
            days_min=bt.days_min,
            days_max=bt.days_max,
            participation_rate=bt.participation_rate,
            spread_decay_bps=bt.spread_decay_bps,
            min_history_points=bt.min_history_points,
            min_events=bt.min_events,
            min_liquidity_usd=bt.min_liquidity_usd,
            telemetry_path=bt.telemetry_path,
            bankroll_usd=bt.bankroll_usd,
            volatility_window_points=bt.volatility_window_points,
            synthetic_orderbook_half_spread_bps=bt.synthetic_orderbook_half_spread_bps,
            synthetic_orderbook_depth_usd=bt.synthetic_orderbook_depth_usd,
        )

    now_ts = int(time.time())
    start_ts = now_ts - (bt.days * 86400)
    end_ts = now_ts

    # Load markets
    if backtest_file:
        markets = _load_backtest_from_file(backtest_file)
        source = "fixture"
    else:
        markets, source = _load_backtest_markets(p, bt, start_ts, end_ts)

    if not markets:
        return {
            "status": "ok",
            "mode": "backtest",
            "results": {
                "starting_bankroll_usd": bt.bankroll_usd,
                "return_pct": 0.0,
                "annualized_return_pct": 0.0,
                "sharpe_score": 0.0,
                "max_drawdown_pct": 0.0,
                "hit_rate": 0.0,
                "total_events": 0,
                "fill_events": 0,
                "pair_count": 0,
            },
            "backtest_summary": {
                "quoted_points": 0,
                "data_source": source,
                "orderbook_modes": {},
            },
            "pairs": [],
            "risk_note": "No markets with sufficient history found.",
            "disclaimer": DISCLAIMER,
            "audit": {"generated_at": _timestamp_iso(), "backtest_days": bt.days},
        }

    # Simulate each pair
    capital_per_pair = bt.bankroll_usd / max(len(markets), 1)
    results: list[dict[str, Any]] = []
    for market in markets:
        result = _simulate_pair(market, p, bt, allocated_capital=capital_per_pair)
        results.append(result)

    # Aggregate
    total_pnl = sum(_safe_float(r.get("pnl_usd"), 0.0) for r in results)
    total_fill_events = sum(_safe_int(r.get("fill_events"), 0) for r in results)
    total_quoted = sum(_safe_int(r.get("quoted_points"), 0) for r in results)
    all_event_pnls: list[float] = []
    for r in results:
        all_event_pnls.extend(r.get("event_pnls", []))

    return_pct = (total_pnl / bt.bankroll_usd) * 100.0 if bt.bankroll_usd > 0 else 0.0
    annualized = return_pct * (365.0 / max(bt.days, 1))
    mean_pnl = sum(all_event_pnls) / len(all_event_pnls) if all_event_pnls else 0.0
    std_pnl = pstdev(all_event_pnls) if len(all_event_pnls) > 1 else abs(mean_pnl) or 1.0
    sharpe = mean_pnl / max(std_pnl, 1e-9)
    wins = sum(1 for e in all_event_pnls if e > 0)
    hit_rate = (wins / len(all_event_pnls) * 100.0) if all_event_pnls else 0.0

    # Max drawdown from aggregated equity curves
    all_equity = [bt.bankroll_usd]
    for r in results:
        curve = r.get("equity_curve", [])
        if len(curve) > 1:
            for val in curve[1:]:
                all_equity.append(all_equity[-1] + (val - curve[0]) / max(len(results), 1))
    peak = all_equity[0]
    max_dd_pct = 0.0
    for eq in all_equity:
        if eq > peak:
            peak = eq
        dd = ((peak - eq) / peak * 100.0) if peak > 0 else 0.0
        if dd > max_dd_pct:
            max_dd_pct = dd

    # Orderbook mode summary
    ob_modes: dict[str, int] = defaultdict(int)
    for r in results:
        mode = _safe_str(r.get("orderbook_mode"), "unknown")
        ob_modes[mode] += 1

    # Min events gate
    risk_note = ""
    if total_fill_events < bt.min_events:
        risk_note = (
            f"Insufficient fill events: {total_fill_events} < min {bt.min_events}. "
            "Backtest results may not be statistically significant."
        )

    # Pair contributions
    ranked = _rank_pair_markets(markets, results, p)
    pair_details = []
    for market, result, score in ranked:
        pair_details.append({
            "market_id": market.get("market_id", ""),
            "pair_market_id": market.get("pair_market_id", ""),
            "question": market.get("question", ""),
            "pair_question": market.get("pair_question", ""),
            "event_ticker": market.get("event_ticker", ""),
            "pnl_usd": round(_safe_float(result.get("pnl_usd"), 0.0), 4),
            "fill_events": _safe_int(result.get("fill_events"), 0),
            "quoted_points": _safe_int(result.get("quoted_points"), 0),
            "sharpe_score": round(score, 4),
            "orderbook_mode": result.get("orderbook_mode", "unknown"),
        })

    # Write telemetry if configured
    if bt.telemetry_path:
        all_telemetry = []
        for r in results:
            all_telemetry.extend(r.get("telemetry", []))
        write_telemetry_records(bt.telemetry_path, all_telemetry)

    return {
        "status": "ok",
        "mode": "backtest",
        "results": {
            "starting_bankroll_usd": bt.bankroll_usd,
            "return_pct": round(return_pct, 4),
            "annualized_return_pct": round(annualized, 4),
            "sharpe_score": round(sharpe, 4),
            "max_drawdown_pct": round(max_dd_pct, 4),
            "hit_rate": round(hit_rate, 2),
            "total_events": len(all_event_pnls),
            "fill_events": total_fill_events,
            "pair_count": len(markets),
        },
        "backtest_summary": {
            "quoted_points": total_quoted,
            "data_source": source,
            "orderbook_modes": dict(ob_modes),
        },
        "pairs": pair_details,
        "risk_note": risk_note,
        "disclaimer": DISCLAIMER,
        "audit": {
            "generated_at": _timestamp_iso(),
            "backtest_days": bt.days,
            "start_ts": start_ts,
            "end_ts": end_ts,
        },
    }


# ---------------------------------------------------------------------------
# Trade mode
# ---------------------------------------------------------------------------

def run_trade(
    config: dict[str, Any],
    yes_live: bool,
    backtest_file: str | None = None,
) -> dict[str, Any]:
    """Trade mode: always runs backtest first, then emits paired trade intents."""
    execution = config.get("execution", {})
    if not isinstance(execution, dict):
        execution = {}
    dry_run = execution.get("dry_run", True)
    live_mode = execution.get("live_mode", False)
    require_positive = execution.get("require_positive_backtest", True)
    max_drawdown_pct = _safe_float(execution.get("max_drawdown_pct"), 15.0)

    # Run backtest first
    backtest_result = run_backtest(config, None, backtest_file=backtest_file)
    if backtest_result.get("status") != "ok":
        return backtest_result

    backtest_return = _safe_float(backtest_result.get("results", {}).get("return_pct"), 0.0)
    if require_positive and backtest_return <= 0:
        return {
            "status": "ok",
            "mode": "trade",
            "run_status": "blocked",
            "reason": f"Backtest return {backtest_return:.2f}% <= 0. Trade mode blocked by require_positive_backtest.",
            "backtest_summary": backtest_result.get("results", {}),
            "trade_intents": [],
            "disclaimer": DISCLAIMER,
            "audit": {"generated_at": _timestamp_iso()},
        }

    # Build trade intents from top-ranked pairs
    p = to_strategy_params(config)
    pairs = backtest_result.get("pairs", [])
    trade_intents: list[dict[str, Any]] = []

    for pair in pairs[:p.pairs_max]:
        basis_entry_cents = int(p.basis_entry_bps / 100.0)
        intent = {
            "market_id": pair["market_id"],
            "pair_market_id": pair["pair_market_id"],
            "question": pair.get("question", ""),
            "pair_question": pair.get("pair_question", ""),
            "event_ticker": pair.get("event_ticker", ""),
            "primary_action": "buy_yes",
            "pair_action": "buy_no",
            "notional_usd": min(p.max_notional_per_pair_usd, p.max_leg_notional_usd),
            "basis_entry_bps": p.basis_entry_bps,
            "basis_exit_bps": p.basis_exit_bps,
            "dry_run": dry_run,
            "live": live_mode and yes_live,
        }
        trade_intents.append(intent)

    # Execute live if configured
    execution_results: list[dict[str, Any]] = []
    if live_mode and yes_live and not dry_run:
        try:
            from kalshi_client import KalshiClient
            client = KalshiClient()
            if not client.is_authenticated:
                return {
                    "status": "error",
                    "error_code": "missing_kalshi_auth",
                    "message": "Kalshi API key and private key required for live trading.",
                    "trade_intents": trade_intents,
                    "disclaimer": DISCLAIMER,
                    "audit": {"generated_at": _timestamp_iso()},
                }

            # Risk guard: check drawdown
            state = config.get("state", {})
            live_risk = state.get("live_risk", {})
            dd_result = check_drawdown_stop_loss(
                live_risk=live_risk,
                max_drawdown_pct=max_drawdown_pct,
                unwind_fn=lambda: _execute_unwind_all(client),
            )
            if dd_result is not None:
                return {
                    "status": "ok",
                    "mode": "trade",
                    "run_status": "unwound",
                    "reason": "Drawdown stop-loss triggered.",
                    "unwind_result": dd_result,
                    "disclaimer": DISCLAIMER,
                    "audit": {"generated_at": _timestamp_iso()},
                }

            for intent in trade_intents:
                try:
                    # Buy primary leg (yes)
                    mid_price_cents = 50  # Default; would be fetched from orderbook in production
                    primary_order = client.create_order(
                        ticker=intent["market_id"],
                        side="yes",
                        action="buy",
                        count=max(1, int(intent["notional_usd"])),
                        type="limit",
                        yes_price=mid_price_cents,
                    )
                    # Buy pair leg (no = hedge)
                    pair_order = client.create_order(
                        ticker=intent["pair_market_id"],
                        side="no",
                        action="buy",
                        count=max(1, int(intent["notional_usd"])),
                        type="limit",
                        no_price=mid_price_cents,
                    )
                    execution_results.append({
                        "market_id": intent["market_id"],
                        "pair_market_id": intent["pair_market_id"],
                        "primary_order": primary_order,
                        "pair_order": pair_order,
                        "status": "submitted",
                    })
                except Exception as exc:
                    execution_results.append({
                        "market_id": intent["market_id"],
                        "pair_market_id": intent["pair_market_id"],
                        "status": "error",
                        "error": str(exc),
                    })
        except ImportError:
            pass

    pairs_considered = len(pairs)
    pairs_quoted = len(trade_intents)

    return {
        "status": "ok",
        "mode": "trade",
        "run_status": "completed",
        "backtest_summary": backtest_result.get("results", {}),
        "strategy_summary": {
            "pairs_considered": pairs_considered,
            "pairs_quoted": pairs_quoted,
        },
        "pair_trades": trade_intents,
        "execution_results": execution_results if execution_results else None,
        "risk_note": backtest_result.get("risk_note", ""),
        "disclaimer": DISCLAIMER,
        "audit": {"generated_at": _timestamp_iso()},
    }


# ---------------------------------------------------------------------------
# Emergency unwind
# ---------------------------------------------------------------------------

def _execute_unwind_all(client: Any) -> dict[str, Any]:
    """Cancel all open orders and sell all positions at market."""
    cancelled = []
    sold = []
    errors = []

    try:
        orders_resp = client.get_orders(status="resting")
        orders = orders_resp.get("orders", [])
        for order in orders:
            order_id = _safe_str(order.get("order_id"), "")
            if order_id:
                try:
                    client.cancel_order(order_id)
                    cancelled.append(order_id)
                except Exception as exc:
                    errors.append(f"cancel {order_id}: {exc}")
    except Exception as exc:
        errors.append(f"get_orders: {exc}")

    try:
        positions_resp = client.get_positions(settlement_status="unsettled")
        positions = positions_resp.get("market_positions", [])
        for pos in positions:
            ticker = _safe_str(pos.get("ticker"), "")
            position_qty = _safe_int(pos.get("position", 0), 0)
            if ticker and position_qty != 0:
                side = "yes" if position_qty > 0 else "no"
                try:
                    client.create_order(
                        ticker=ticker,
                        side=side,
                        action="sell",
                        count=abs(position_qty),
                        type="market",
                    )
                    sold.append(ticker)
                except Exception as exc:
                    errors.append(f"sell {ticker}: {exc}")
    except Exception as exc:
        errors.append(f"get_positions: {exc}")

    return {
        "cancelled_orders": cancelled,
        "sold_positions": sold,
        "errors": errors,
        "timestamp": _timestamp_iso(),
    }


def run_unwind_all(config: dict[str, Any]) -> dict[str, Any]:
    """Emergency: cancel all orders, market-sell all positions."""
    try:
        from kalshi_client import KalshiClient
        client = KalshiClient()
        if not client.is_authenticated:
            return {
                "status": "error",
                "error_code": "missing_kalshi_auth",
                "message": "Kalshi credentials required for emergency unwind.",
            }
        result = _execute_unwind_all(client)
        return {
            "status": "ok",
            "mode": "unwind",
            "unwind_result": result,
            "audit": {"generated_at": _timestamp_iso()},
        }
    except ImportError:
        return {
            "status": "error",
            "error_code": "import_error",
            "message": "kalshi_client module not found.",
        }
    except Exception as exc:
        return {
            "status": "error",
            "error_code": "unwind_error",
            "message": str(exc),
        }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    args = parse_args()
    config = load_config(args.config)

    if args.unwind_all:
        if not args.yes_live:
            result = {
                "status": "error",
                "error_code": "missing_confirmation",
                "message": "Emergency unwind requires --yes-live flag.",
            }
        else:
            result = run_unwind_all(config)
        print(json.dumps(result, indent=2))
        return 0 if result.get("status") == "ok" else 1

    if args.run_type == "backtest":
        result = run_backtest(config, args.backtest_days, backtest_file=args.backtest_file)
    else:
        # Trade mode: runs backtest first, then emits trade intents
        backtest_result = run_backtest(config, args.backtest_days, backtest_file=args.backtest_file)
        execution = config.get("execution", {})
        require_positive = execution.get("require_positive_backtest", True)
        backtest_return = _safe_float(
            backtest_result.get("results", {}).get("return_pct"), 0.0,
        )

        if require_positive and backtest_return <= 0:
            result = {
                "status": "ok",
                "mode": "trade",
                "run_status": "blocked",
                "reason": f"Backtest return {backtest_return:.2f}% <= 0. Trade blocked.",
                "backtest_summary": backtest_result.get("results", {}),
                "trade_intents": [],
                "disclaimer": DISCLAIMER,
                "audit": {"generated_at": _timestamp_iso()},
            }
        else:
            result = run_trade(config, args.yes_live, backtest_file=args.backtest_file)

    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
