#!/usr/bin/env python3
"""Rebate-aware maker strategy scaffold for Polymarket binary markets."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class StrategyParams:
    bankroll_usd: float = 1000.0
    markets_max: int = 8
    min_seconds_to_resolution: int = 6 * 60 * 60
    min_edge_bps: float = 2.0
    default_rebate_bps: float = 3.0
    expected_unwind_cost_bps: float = 1.5
    adverse_selection_bps: float = 1.0
    min_spread_bps: float = 20.0
    max_spread_bps: float = 150.0
    volatility_spread_multiplier: float = 0.35
    base_order_notional_usd: float = 25.0
    max_notional_per_market_usd: float = 125.0
    max_total_notional_usd: float = 500.0
    max_position_notional_usd: float = 150.0
    inventory_skew_strength_bps: float = 25.0


@dataclass(frozen=True)
class BacktestParams:
    days: int = 90
    fidelity_minutes: int = 60
    participation_rate: float = 0.2
    volatility_window_points: int = 24
    min_liquidity_usd: float = 100000.0
    markets_fetch_limit: int = 300
    min_history_points: int = 480
    gamma_markets_url: str = "https://gamma-api.polymarket.com/markets"
    clob_history_url: str = "https://clob.polymarket.com/prices-history"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Polymarket maker/rebate strategy.")
    parser.add_argument("--config", default="config.json", help="Config file path.")
    parser.add_argument(
        "--markets-file",
        default=None,
        help="Optional path to market snapshot JSON file.",
    )
    parser.add_argument(
        "--run-type",
        default="backtest",
        choices=("quote", "monitor", "backtest"),
        help="Run type. Use backtest to run a 90-day replay before executing quotes.",
    )
    parser.add_argument(
        "--yes-live",
        action="store_true",
        help="Explicit live execution confirmation flag.",
    )
    parser.add_argument(
        "--backtest-file",
        default=None,
        help="Optional path to pre-saved backtest market history JSON.",
    )
    parser.add_argument(
        "--backtest-days",
        type=int,
        default=None,
        help="Override backtest lookback window in days (default from config: 90).",
    )
    return parser.parse_args()


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(config_path: str) -> dict[str, Any]:
    return load_json_file(Path(config_path))


def load_markets(config: dict[str, Any], markets_file: str | None) -> list[dict[str, Any]]:
    if markets_file:
        payload = load_json_file(Path(markets_file))
        if isinstance(payload, dict) and isinstance(payload.get("markets"), list):
            return payload["markets"]
        if isinstance(payload, list):
            return payload
        return []
    return list(config.get("markets", []))


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


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def to_params(config: dict[str, Any]) -> StrategyParams:
    strategy = config.get("strategy", {})
    return StrategyParams(
        bankroll_usd=_safe_float(strategy.get("bankroll_usd"), 1000.0),
        markets_max=_safe_int(strategy.get("markets_max"), 8),
        min_seconds_to_resolution=_safe_int(strategy.get("min_seconds_to_resolution"), 21600),
        min_edge_bps=_safe_float(strategy.get("min_edge_bps"), 2.0),
        default_rebate_bps=_safe_float(strategy.get("default_rebate_bps"), 3.0),
        expected_unwind_cost_bps=_safe_float(strategy.get("expected_unwind_cost_bps"), 1.5),
        adverse_selection_bps=_safe_float(strategy.get("adverse_selection_bps"), 1.0),
        min_spread_bps=_safe_float(strategy.get("min_spread_bps"), 20.0),
        max_spread_bps=_safe_float(strategy.get("max_spread_bps"), 150.0),
        volatility_spread_multiplier=_safe_float(
            strategy.get("volatility_spread_multiplier"),
            0.35,
        ),
        base_order_notional_usd=_safe_float(strategy.get("base_order_notional_usd"), 25.0),
        max_notional_per_market_usd=_safe_float(strategy.get("max_notional_per_market_usd"), 125.0),
        max_total_notional_usd=_safe_float(strategy.get("max_total_notional_usd"), 500.0),
        max_position_notional_usd=_safe_float(strategy.get("max_position_notional_usd"), 150.0),
        inventory_skew_strength_bps=_safe_float(strategy.get("inventory_skew_strength_bps"), 25.0),
    )


def to_backtest_params(config: dict[str, Any]) -> BacktestParams:
    backtest = config.get("backtest", {})
    return BacktestParams(
        days=max(1, _safe_int(backtest.get("days"), 90)),
        fidelity_minutes=max(1, _safe_int(backtest.get("fidelity_minutes"), 60)),
        participation_rate=clamp(
            _safe_float(backtest.get("participation_rate"), 0.2),
            0.0,
            1.0,
        ),
        volatility_window_points=max(3, _safe_int(backtest.get("volatility_window_points"), 24)),
        min_liquidity_usd=max(0.0, _safe_float(backtest.get("min_liquidity_usd"), 100000.0)),
        markets_fetch_limit=max(1, _safe_int(backtest.get("markets_fetch_limit"), 300)),
        min_history_points=max(10, _safe_int(backtest.get("min_history_points"), 480)),
        gamma_markets_url=_safe_str(
            backtest.get("gamma_markets_url"),
            "https://gamma-api.polymarket.com/markets",
        ),
        clob_history_url=_safe_str(
            backtest.get("clob_history_url"),
            "https://clob.polymarket.com/prices-history",
        ),
    )


def compute_spread_bps(volatility_bps: float, p: StrategyParams) -> float:
    spread = p.min_spread_bps + volatility_bps * p.volatility_spread_multiplier
    return clamp(spread, p.min_spread_bps, p.max_spread_bps)


def expected_edge_bps(spread_bps: float, rebate_bps: float, p: StrategyParams) -> float:
    half_spread_capture = spread_bps / 2.0
    return half_spread_capture + rebate_bps - p.expected_unwind_cost_bps - p.adverse_selection_bps


def should_skip_market(market: dict[str, Any], p: StrategyParams) -> tuple[bool, str]:
    ttl = _safe_int(market.get("seconds_to_resolution"), 0)
    if ttl < p.min_seconds_to_resolution:
        return True, "near_resolution"

    mid = _safe_float(market.get("mid_price"), -1.0)
    if mid <= 0.01 or mid >= 0.99:
        return True, "extreme_probability"

    bid = _safe_float(market.get("best_bid"), -1.0)
    ask = _safe_float(market.get("best_ask"), -1.0)
    if not (0.0 <= bid <= 1.0 and 0.0 <= ask <= 1.0 and bid <= ask):
        return True, "invalid_book"

    return False, ""


def _parse_iso_ts(value: Any) -> int | None:
    raw = _safe_str(value, "")
    if not raw:
        return None
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _json_to_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            return []
    return []


def _http_get_json(url: str, timeout: int = 30) -> dict[str, Any] | list[Any]:
    request = Request(
        url,
        headers={
            "User-Agent": "seren-maker-rebate-bot/1.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _normalize_history(
    history_payload: list[Any],
    start_ts: int,
    end_ts: int,
) -> list[tuple[int, float]]:
    cleaned: list[tuple[int, float]] = []
    seen: set[int] = set()
    for point in history_payload:
        t: int | None = None
        p: float | None = None
        if isinstance(point, dict):
            t = _safe_int(point.get("t"), -1)
            p = _safe_float(point.get("p"), -1.0)
        elif isinstance(point, list | tuple) and len(point) >= 2:
            t = _safe_int(point[0], -1)
            p = _safe_float(point[1], -1.0)

        if t is None or p is None:
            continue
        if t < 0 or not (0.0 <= p <= 1.0):
            continue
        if t < start_ts or t > end_ts or t in seen:
            continue
        seen.add(t)
        cleaned.append((t, p))
    cleaned.sort(key=lambda x: x[0])
    return cleaned


def _load_markets_from_fixture(
    payload: dict[str, Any] | list[Any],
    start_ts: int,
    end_ts: int,
) -> list[dict[str, Any]]:
    raw_markets: list[Any]
    if isinstance(payload, dict):
        raw_markets = _json_to_list(payload.get("markets"))
    elif isinstance(payload, list):
        raw_markets = payload
    else:
        raw_markets = []

    markets: list[dict[str, Any]] = []
    for raw in raw_markets:
        if not isinstance(raw, dict):
            continue
        history = _normalize_history(
            history_payload=_json_to_list(raw.get("history")),
            start_ts=start_ts,
            end_ts=end_ts,
        )
        if len(history) < 2:
            continue
        market_id = _safe_str(raw.get("market_id"), _safe_str(raw.get("token_id"), "unknown"))
        markets.append(
            {
                "market_id": market_id,
                "question": _safe_str(raw.get("question"), market_id),
                "token_id": _safe_str(raw.get("token_id"), market_id),
                "end_ts": _safe_int(raw.get("end_ts"), _parse_iso_ts(raw.get("endDate")) or 0),
                "rebate_bps": _safe_float(raw.get("rebate_bps"), 0.0),
                "history": history,
                "source": "fixture",
            }
        )
    return markets


def _fetch_live_markets(
    strategy_params: StrategyParams,
    backtest_params: BacktestParams,
    start_ts: int,
    end_ts: int,
) -> list[dict[str, Any]]:
    query = urlencode(
        {
            "active": "true",
            "closed": "false",
            "limit": backtest_params.markets_fetch_limit,
            "order": "volume24hr",
            "ascending": "false",
        }
    )
    raw = _http_get_json(f"{backtest_params.gamma_markets_url}?{query}")
    if not isinstance(raw, list):
        return []

    candidates: list[dict[str, Any]] = []
    for market in raw:
        if not isinstance(market, dict):
            continue
        liquidity = _safe_float(market.get("liquidity"), 0.0)
        if liquidity < backtest_params.min_liquidity_usd:
            continue
        end_market = _parse_iso_ts(market.get("endDate")) or 0
        if end_market <= start_ts + strategy_params.min_seconds_to_resolution:
            continue
        token_ids = _json_to_list(market.get("clobTokenIds"))
        if not token_ids:
            continue
        token_id = _safe_str(token_ids[0], "")
        if not token_id:
            continue
        candidates.append(
            {
                "market_id": _safe_str(market.get("id"), token_id),
                "question": _safe_str(market.get("question"), token_id),
                "token_id": token_id,
                "end_ts": end_market,
                "rebate_bps": _safe_float(market.get("rebate_bps"), 0.0),
                "volume24hr": _safe_float(market.get("volume24hr"), 0.0),
            }
        )

    selected: list[dict[str, Any]] = []
    for candidate in candidates:
        if len(selected) >= strategy_params.markets_max:
            break
        history_query = urlencode(
            {
                "market": candidate["token_id"],
                "interval": "max",
                "fidelity": backtest_params.fidelity_minutes,
            }
        )
        payload = _http_get_json(f"{backtest_params.clob_history_url}?{history_query}")
        if not isinstance(payload, dict):
            continue
        history = _normalize_history(
            history_payload=_json_to_list(payload.get("history")),
            start_ts=start_ts,
            end_ts=end_ts,
        )
        if len(history) < backtest_params.min_history_points:
            continue
        selected.append(
            {
                **candidate,
                "history": history,
                "source": "live-api",
            }
        )
    return selected


def _max_drawdown(equity_curve: list[float]) -> float:
    peak = float("-inf")
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        max_dd = max(max_dd, peak - value)
    return max_dd


def _simulate_market_backtest(
    market: dict[str, Any],
    strategy_params: StrategyParams,
    backtest_params: BacktestParams,
) -> dict[str, Any]:
    history: list[tuple[int, float]] = market["history"]
    window = backtest_params.volatility_window_points
    if len(history) < window + 2:
        return {
            "market_id": market["market_id"],
            "question": market["question"],
            "considered_points": 0,
            "quoted_points": 0,
            "skipped_points": 0,
            "filled_notional_usd": 0.0,
            "pnl_usd": 0.0,
            "event_pnls": [],
        }

    moves_bps = [
        abs((history[i][1] - history[i - 1][1]) * 10000.0)
        for i in range(1, len(history))
    ]
    rebate_bps = _safe_float(market.get("rebate_bps"), strategy_params.default_rebate_bps)
    if rebate_bps <= 0:
        rebate_bps = strategy_params.default_rebate_bps
    end_ts = _safe_int(market.get("end_ts"), 0)

    considered = 0
    quoted = 0
    skipped = 0
    filled_notional = 0.0
    pnl = 0.0
    event_pnls: list[float] = []

    for i in range(window, len(history) - 1):
        t, mid_price = history[i]
        _, next_price = history[i + 1]
        considered += 1

        if end_ts and end_ts - t < strategy_params.min_seconds_to_resolution:
            skipped += 1
            continue
        if mid_price <= 0.01 or mid_price >= 0.99:
            skipped += 1
            continue

        vol_slice = moves_bps[i - window : i]
        vol_bps = pstdev(vol_slice) if len(vol_slice) > 1 else strategy_params.min_spread_bps
        spread_bps = compute_spread_bps(vol_bps, strategy_params)
        expected_edge = expected_edge_bps(spread_bps, rebate_bps, strategy_params)
        if expected_edge < strategy_params.min_edge_bps:
            skipped += 1
            continue

        quoted += 1
        half_spread_bps = spread_bps / 2.0
        next_move_bps = abs((next_price - mid_price) * 10000.0)
        touch_ratio = min(1.0, next_move_bps / max(half_spread_bps, 1e-9))
        fill_fraction = backtest_params.participation_rate * touch_ratio
        event_notional = strategy_params.base_order_notional_usd * fill_fraction

        extra_pickoff_bps = max(0.0, next_move_bps - half_spread_bps)
        realized_edge_bps = (
            half_spread_bps
            + rebate_bps
            - strategy_params.expected_unwind_cost_bps
            - strategy_params.adverse_selection_bps
            - extra_pickoff_bps
        )
        event_pnl = event_notional * realized_edge_bps / 10000.0
        filled_notional += event_notional
        pnl += event_pnl
        event_pnls.append(event_pnl)

    return {
        "market_id": market["market_id"],
        "question": market["question"],
        "considered_points": considered,
        "quoted_points": quoted,
        "skipped_points": skipped,
        "filled_notional_usd": round(filled_notional, 4),
        "pnl_usd": round(pnl, 6),
        "event_pnls": event_pnls,
    }


def run_backtest(
    config: dict[str, Any],
    backtest_file: str | None,
    backtest_days_override: int | None,
) -> dict[str, Any]:
    strategy_params = to_params(config)
    backtest_params = to_backtest_params(config)
    days = max(1, backtest_days_override or backtest_params.days)
    end_ts = int(time.time())
    start_ts = end_ts - (days * 24 * 3600)

    try:
        if backtest_file:
            fixture_payload = load_json_file(Path(backtest_file))
            markets = _load_markets_from_fixture(
                payload=fixture_payload,
                start_ts=start_ts,
                end_ts=end_ts,
            )
            source = "file"
        elif config.get("backtest_markets"):
            markets = _load_markets_from_fixture(
                payload=config.get("backtest_markets", []),
                start_ts=start_ts,
                end_ts=end_ts,
            )
            source = "config"
        else:
            markets = _fetch_live_markets(
                strategy_params=strategy_params,
                backtest_params=backtest_params,
                start_ts=start_ts,
                end_ts=end_ts,
            )
            source = "live-api"
    except Exception as exc:  # pragma: no cover - defensive runtime path
        return {
            "status": "error",
            "error_code": "backtest_data_load_failed",
            "message": str(exc),
            "hint": (
                "Provide --backtest-file with pre-saved history JSON if "
                "network/API access is blocked."
            ),
            "dry_run": True,
        }

    if not markets:
        return {
            "status": "error",
            "error_code": "no_backtest_markets",
            "message": "No markets with sufficient history were available for backtest.",
            "dry_run": True,
        }

    market_summaries: list[dict[str, Any]] = []
    event_pnls: list[float] = []
    total_considered = 0
    total_quoted = 0
    total_notional = 0.0

    for market in markets[: strategy_params.markets_max]:
        summary = _simulate_market_backtest(
            market=market,
            strategy_params=strategy_params,
            backtest_params=backtest_params,
        )
        market_summaries.append(
            {
                "market_id": summary["market_id"],
                "question": summary["question"],
                "considered_points": summary["considered_points"],
                "quoted_points": summary["quoted_points"],
                "skipped_points": summary["skipped_points"],
                "filled_notional_usd": summary["filled_notional_usd"],
                "pnl_usd": summary["pnl_usd"],
            }
        )
        total_considered += int(summary["considered_points"])
        total_quoted += int(summary["quoted_points"])
        total_notional += float(summary["filled_notional_usd"])
        event_pnls.extend(summary["event_pnls"])

    equity_curve = [strategy_params.bankroll_usd]
    running_equity = strategy_params.bankroll_usd
    for event_pnl in event_pnls:
        running_equity += event_pnl
        equity_curve.append(running_equity)

    ending_equity = running_equity
    total_pnl = ending_equity - strategy_params.bankroll_usd
    return_pct = (total_pnl / strategy_params.bankroll_usd) * 100.0
    max_drawdown = _max_drawdown(equity_curve)
    decision = "consider_live_guarded" if total_pnl > 0 else "paper_only_or_tune"

    return {
        "status": "ok",
        "skill": "polymarket-maker-rebate-bot",
        "mode": "backtest",
        "dry_run": True,
        "backtest_summary": {
            "days": days,
            "source": source,
            "start_utc": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
            "end_utc": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
            "markets_selected": len(market_summaries),
            "considered_points": total_considered,
            "quoted_points": total_quoted,
            "quote_rate_pct": round(
                (total_quoted / total_considered) * 100.0 if total_considered else 0.0,
                4,
            ),
        },
        "results": {
            "starting_bankroll_usd": round(strategy_params.bankroll_usd, 4),
            "ending_bankroll_usd": round(ending_equity, 4),
            "total_pnl_usd": round(total_pnl, 4),
            "return_pct": round(return_pct, 4),
            "filled_notional_usd": round(total_notional, 4),
            "events": len(event_pnls),
            "max_drawdown_usd": round(max_drawdown, 4),
            "decision_hint": decision,
            "disclaimer": (
                "Backtests are estimates and do not guarantee future performance."
            ),
        },
        "markets": sorted(market_summaries, key=lambda item: item["pnl_usd"], reverse=True),
        "next_steps": [
            "Review negative-PnL markets and edge assumptions.",
            "Tune spread, participation, and risk caps before live mode.",
            "Run quote mode only after backtest results are acceptable.",
        ],
    }


def quote_market(
    market: dict[str, Any],
    inventory_notional: float,
    outstanding_notional: float,
    p: StrategyParams,
) -> dict[str, Any]:
    market_id = str(market.get("market_id", "unknown"))
    mid = _safe_float(market.get("mid_price"), 0.5)
    vol_bps = _safe_float(market.get("volatility_bps"), p.min_spread_bps)
    rebate_bps = _safe_float(market.get("rebate_bps"), p.default_rebate_bps)
    spread_bps = compute_spread_bps(vol_bps, p)
    edge_bps = expected_edge_bps(spread_bps, rebate_bps, p)

    if edge_bps < p.min_edge_bps:
        return {
            "market_id": market_id,
            "status": "skipped",
            "reason": "negative_or_thin_edge",
            "edge_bps": round(edge_bps, 3),
        }

    # Positive inventory -> lower ask / higher bid to de-risk longs.
    inventory_ratio = 0.0
    if p.max_position_notional_usd > 0:
        inventory_ratio = clamp(
            inventory_notional / p.max_position_notional_usd,
            -1.0,
            1.0,
        )
    skew_bps = -inventory_ratio * p.inventory_skew_strength_bps
    half_spread_prob = (spread_bps / 2.0) / 10000.0
    skew_prob = skew_bps / 10000.0

    bid_px = clamp(mid - half_spread_prob + skew_prob, 0.001, 0.999)
    ask_px = clamp(mid + half_spread_prob + skew_prob, 0.001, 0.999)
    if bid_px >= ask_px:
        return {
            "market_id": market_id,
            "status": "skipped",
            "reason": "crossed_quote_after_skew",
            "edge_bps": round(edge_bps, 3),
        }

    remaining_market = max(0.0, p.max_notional_per_market_usd - abs(inventory_notional))
    remaining_total = max(0.0, p.max_total_notional_usd - max(0.0, outstanding_notional))
    quote_notional = min(p.base_order_notional_usd, remaining_market, remaining_total)

    if quote_notional <= 0:
        return {
            "market_id": market_id,
            "status": "skipped",
            "reason": "risk_capacity_exhausted",
            "edge_bps": round(edge_bps, 3),
        }

    return {
        "market_id": market_id,
        "status": "quoted",
        "edge_bps": round(edge_bps, 3),
        "spread_bps": round(spread_bps, 3),
        "rebate_bps": round(rebate_bps, 3),
        "quote_notional_usd": round(quote_notional, 2),
        "bid_price": round(bid_px, 4),
        "ask_price": round(ask_px, 4),
        "inventory_notional_usd": round(inventory_notional, 2),
    }


def run_once(
    config: dict[str, Any],
    markets: list[dict[str, Any]],
    yes_live: bool,
) -> dict[str, Any]:
    params = to_params(config)
    execution = config.get("execution", {})
    live_mode = bool(execution.get("live_mode", False))
    dry_run = bool(execution.get("dry_run", True))

    # Hard safety rail: both config + CLI flag are required.
    if live_mode and not yes_live:
        return {
            "status": "error",
            "error_code": "live_confirmation_required",
            "message": "Set --yes-live to enable live execution.",
            "dry_run": True,
        }

    if live_mode and dry_run:
        return {
            "status": "error",
            "error_code": "invalid_execution_mode",
            "message": "dry_run must be false when live_mode is true.",
            "dry_run": True,
        }

    inventory = config.get("state", {}).get("inventory", {})
    inventory_notional_by_market = {
        str(k): _safe_float(v, 0.0) for k, v in inventory.items()
    }

    proposals: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    outstanding_notional = 0.0
    selected = 0

    for market in markets:
        if selected >= params.markets_max:
            break

        skip, reason = should_skip_market(market, params)
        market_id = str(market.get("market_id", "unknown"))
        if skip:
            rejected.append({"market_id": market_id, "reason": reason})
            continue

        inv = inventory_notional_by_market.get(market_id, 0.0)
        proposal = quote_market(
            market=market,
            inventory_notional=inv,
            outstanding_notional=outstanding_notional,
            p=params,
        )
        if proposal.get("status") == "quoted":
            outstanding_notional += float(proposal["quote_notional_usd"])
            proposals.append(proposal)
            selected += 1
        else:
            rejected.append(
                {
                    "market_id": market_id,
                    "reason": proposal.get("reason", "unknown"),
                    "edge_bps": proposal.get("edge_bps"),
                }
            )

    mode = "live" if live_mode and yes_live and not dry_run else "dry-run"
    return {
        "status": "ok",
        "skill": "polymarket-maker-rebate-bot",
        "mode": mode,
        "dry_run": mode != "live",
        "strategy_summary": {
            "bankroll_usd": params.bankroll_usd,
            "markets_considered": len(markets),
            "markets_quoted": len(proposals),
            "markets_skipped": len(rejected),
            "outstanding_notional_usd": round(outstanding_notional, 2),
            "min_edge_bps": params.min_edge_bps,
        },
        "quotes": proposals,
        "skips": rejected,
    }


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if args.run_type == "backtest":
        result = run_backtest(
            config=config,
            backtest_file=args.backtest_file,
            backtest_days_override=args.backtest_days,
        )
    else:
        markets = load_markets(config=config, markets_file=args.markets_file)
        result = run_once(config=config, markets=markets, yes_live=args.yes_live)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
