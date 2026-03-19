#!/usr/bin/env python3
"""Rebate-aware maker strategy scaffold for Polymarket binary markets."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from polymarket_live import (
    DEFAULT_STALE_ORDER_MAX_AGE_SECONDS,
    DEFAULT_UNWIND_BEFORE_RESOLUTION_SECONDS,
    DirectClobTrader,
    cancel_stale_orders,
    execute_single_market_quotes,
    inject_held_position_markets,
    live_settings_from_execution,
    load_live_single_markets,
    positions_by_key,
    single_market_inventory_notional,
)

SEREN_POLYMARKET_PUBLISHER_HOST = "api.serendb.com"
SEREN_PUBLISHERS_PREFIX = "/publishers/"
SEREN_POLYMARKET_PUBLISHER_PREFIX = f"https://{SEREN_POLYMARKET_PUBLISHER_HOST}{SEREN_PUBLISHERS_PREFIX}"
SEREN_POLYMARKET_DATA_PUBLISHER = "polymarket-data"
SEREN_POLYMARKET_DATA_URL_PREFIX = (
    f"https://{SEREN_POLYMARKET_PUBLISHER_HOST}{SEREN_PUBLISHERS_PREFIX}{SEREN_POLYMARKET_DATA_PUBLISHER}"
)
POLYMARKET_CLOB_BASE_URL = "https://clob.polymarket.com"
SEREN_PREDICTIONS_PUBLISHER = "seren-polymarket-intelligence"
SEREN_PREDICTIONS_URL_PREFIX = (
    f"https://api.serendb.com/publishers/{SEREN_PREDICTIONS_PUBLISHER}"
)
SEREN_ALLOWED_POLYMARKET_PUBLISHERS = frozenset(
    {SEREN_POLYMARKET_DATA_PUBLISHER}
)
POLICY_VIOLATION_BACKTEST_SOURCE = "policy_violation: backtest data source must use Seren Polymarket publisher"
MISSING_RUNTIME_AUTH_ERROR = (
    "missing_runtime_auth: set API_KEY (Seren Desktop runtime) or SEREN_API_KEY; "
    "missing_seren_api_key: set SEREN_API_KEY"
)


@dataclass(frozen=True)
class StrategyParams:
    bankroll_usd: float = 1000.0
    markets_max: int = 12
    min_seconds_to_resolution: int = 6 * 60 * 60
    min_edge_bps: float = 2.0
    default_rebate_bps: float = 3.0
    expected_unwind_cost_bps: float = 1.5
    adverse_selection_bps: float = 1.0
    min_spread_bps: float = 20.0
    max_spread_bps: float = 150.0
    volatility_spread_multiplier: float = 0.35
    base_order_notional_usd: float = 100.0
    max_notional_per_market_usd: float = 300.0
    max_total_notional_usd: float = 1400.0
    max_position_notional_usd: float = 300.0
    inventory_skew_strength_bps: float = 25.0


@dataclass(frozen=True)
class BacktestParams:
    bankroll_usd: float = 100.0
    days: int = 90
    fidelity_minutes: int = 60
    participation_rate: float = 0.6
    volatility_window_points: int = 24
    min_liquidity_usd: float = 25000.0
    markets_fetch_limit: int = 500
    min_history_points: int = 480
    require_orderbook_history: bool = False
    spread_decay_bps: float = 45.0
    join_best_queue_factor: float = 0.85
    off_best_queue_factor: float = 0.35
    synthetic_orderbook_half_spread_bps: float = 18.0
    synthetic_orderbook_depth_usd: float = 125.0
    telemetry_path: str = "logs/polymarket-maker-rebate-backtest-telemetry.jsonl"
    gamma_markets_url: str = f"{SEREN_POLYMARKET_DATA_URL_PREFIX}/markets"
    clob_history_url: str = f"{POLYMARKET_CLOB_BASE_URL}/prices-history"
    # Seren Predictions intelligence (costs SerenBucks per call)
    predictions_enabled: bool = False
    predictions_divergence_url: str = f"{SEREN_PREDICTIONS_URL_PREFIX}/api/oracle/divergence/batch"
    predictions_consensus_url: str = f"{SEREN_PREDICTIONS_URL_PREFIX}/api/oracle/consensus/batch"
    predictions_skew_strength_bps: float = 15.0  # max directional skew from predictions
    predictions_score_boost: float = 0.3  # mm_score boost for divergent markets


@dataclass(frozen=True)
class OptimizationParams:
    enabled: bool = True
    target_return_pct: float = 25.0
    max_iterations: int = 15


@dataclass(frozen=True)
class OrderBookSnapshot:
    t: int
    best_bid: float
    best_ask: float
    bid_size_usd: float
    ask_size_usd: float


@dataclass(frozen=True)
class QuotePlan:
    status: str
    market_id: str
    edge_bps: float
    spread_bps: float
    rebate_bps: float
    bid_price: float = 0.0
    ask_price: float = 0.0
    bid_notional_usd: float = 0.0
    ask_notional_usd: float = 0.0
    inventory_notional_usd: float = 0.0
    reason: str = ""


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
    parser.add_argument(
        "--unwind-all",
        action="store_true",
        help="Emergency liquidation: cancel all orders and market-sell all positions. Requires --yes-live.",
    )
    return parser.parse_args()


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(config_path: str) -> dict[str, Any]:
    return load_json_file(Path(config_path))


def _clone_config(config: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(config))


def _write_config(config_path: str, config: dict[str, Any]) -> None:
    Path(config_path).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def _apply_config_updates_in_place(config: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value


def _persist_runtime_state(config_path: str, config: dict[str, Any], state: dict[str, Any]) -> None:
    if not isinstance(state, dict) or not state:
        return
    current_state = config.get("state")
    if not isinstance(current_state, dict):
        current_state = {}
        config["state"] = current_state
    current_state.update(state)
    Path(config_path).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def _coerce_market_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get("markets", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def load_markets(config: dict[str, Any], markets_file: str | None) -> list[dict[str, Any]]:
    if markets_file:
        return _coerce_market_rows(load_json_file(Path(markets_file)))
    configured_markets = _coerce_market_rows(config.get("markets", []))
    if configured_markets:
        return configured_markets
    return _fetch_live_quote_markets(config)


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


def _extract_live_mid_price(payload: dict[str, Any]) -> float:
    for key in (
        "mid_price",
        "midPrice",
        "midpoint",
        "price",
        "lastTradePrice",
        "last_trade_price",
    ):
        candidate = _normalize_probability(payload.get(key))
        if 0.0 <= candidate <= 1.0:
            return candidate
    outcome_prices = _json_to_list(payload.get("outcomePrices"))
    if outcome_prices:
        candidate = _normalize_probability(outcome_prices[0])
        if 0.0 <= candidate <= 1.0:
            return candidate
    return -1.0


def _extract_live_book(payload: dict[str, Any], mid_price: float) -> tuple[float, float]:
    bid = _normalize_probability(payload.get("best_bid"))
    if not (0.0 <= bid <= 1.0):
        bid = _normalize_probability(payload.get("bestBid"))

    ask = _normalize_probability(payload.get("best_ask"))
    if not (0.0 <= ask <= 1.0):
        ask = _normalize_probability(payload.get("bestAsk"))

    if not (0.0 <= bid <= 1.0):
        bid = mid_price
    if not (0.0 <= ask <= 1.0):
        ask = mid_price
    if bid > ask:
        bid = mid_price
        ask = mid_price
    return bid, ask


def _canonicalize_history_url(url: str) -> str:
    trimmed = url.rstrip("/")
    if trimmed.endswith("/trades"):
        return trimmed[: -len("/trades")] + "/prices-history"
    return url


def to_params(config: dict[str, Any]) -> StrategyParams:
    strategy = config.get("strategy", {})
    return StrategyParams(
        bankroll_usd=_safe_float(strategy.get("bankroll_usd"), 1000.0),
        markets_max=_safe_int(strategy.get("markets_max"), 12),
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
        base_order_notional_usd=_safe_float(strategy.get("base_order_notional_usd"), 100.0),
        max_notional_per_market_usd=_safe_float(strategy.get("max_notional_per_market_usd"), 300.0),
        max_total_notional_usd=_safe_float(strategy.get("max_total_notional_usd"), 1400.0),
        max_position_notional_usd=_safe_float(strategy.get("max_position_notional_usd"), 300.0),
        inventory_skew_strength_bps=_safe_float(strategy.get("inventory_skew_strength_bps"), 25.0),
    )


def to_backtest_params(config: dict[str, Any]) -> BacktestParams:
    backtest = config.get("backtest", {})
    return BacktestParams(
        bankroll_usd=max(1.0, _safe_float(backtest.get("bankroll_usd"), 100.0)),
        days=max(1, _safe_int(backtest.get("days"), 90)),
        fidelity_minutes=max(1, _safe_int(backtest.get("fidelity_minutes"), 60)),
        participation_rate=clamp(
            _safe_float(backtest.get("participation_rate"), 0.6),
            0.0,
            1.0,
        ),
        volatility_window_points=max(3, _safe_int(backtest.get("volatility_window_points"), 24)),
        min_liquidity_usd=max(0.0, _safe_float(backtest.get("min_liquidity_usd"), 25000.0)),
        markets_fetch_limit=max(1, _safe_int(backtest.get("markets_fetch_limit"), 500)),
        min_history_points=max(10, _safe_int(backtest.get("min_history_points"), 480)),
        require_orderbook_history=bool(backtest.get("require_orderbook_history", False)),
        spread_decay_bps=max(1.0, _safe_float(backtest.get("spread_decay_bps"), 45.0)),
        join_best_queue_factor=clamp(
            _safe_float(backtest.get("join_best_queue_factor"), 0.85),
            0.0,
            1.0,
        ),
        off_best_queue_factor=clamp(
            _safe_float(backtest.get("off_best_queue_factor"), 0.35),
            0.0,
            1.0,
        ),
        synthetic_orderbook_half_spread_bps=max(
            1.0,
            _safe_float(backtest.get("synthetic_orderbook_half_spread_bps"), 18.0),
        ),
        synthetic_orderbook_depth_usd=max(
            1.0,
            _safe_float(backtest.get("synthetic_orderbook_depth_usd"), 125.0),
        ),
        telemetry_path=_safe_str(
            backtest.get("telemetry_path"),
            "logs/polymarket-maker-rebate-backtest-telemetry.jsonl",
        ),
        gamma_markets_url=_safe_str(
            backtest.get("gamma_markets_url"),
            f"{SEREN_POLYMARKET_DATA_URL_PREFIX}/markets",
        ),
        clob_history_url=_canonicalize_history_url(
            _safe_str(
                backtest.get("clob_history_url"),
                f"{POLYMARKET_CLOB_BASE_URL}/prices-history",
            )
        ),
        predictions_enabled=bool(backtest.get("predictions_enabled", False)),
        predictions_skew_strength_bps=max(
            0.0, _safe_float(backtest.get("predictions_skew_strength_bps"), 15.0)
        ),
        predictions_score_boost=clamp(
            _safe_float(backtest.get("predictions_score_boost"), 0.3), 0.0, 1.0
        ),
    )


def to_optimization_params(config: dict[str, Any]) -> OptimizationParams:
    backtest = config.get("backtest", {})
    raw = backtest.get("optimization", {}) if isinstance(backtest, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    return OptimizationParams(
        enabled=bool(raw.get("enabled", True)),
        target_return_pct=_safe_float(raw.get("target_return_pct"), 25.0),
        max_iterations=max(1, _safe_int(raw.get("max_iterations"), 15)),
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


def _coerce_unix_ts(value: Any) -> int:
    if isinstance(value, (int, float)):
        ts = int(value)
        if ts > 10_000_000_000:
            ts //= 1000
        return ts
    raw = _safe_str(value, "").strip()
    if not raw:
        return -1
    if raw.isdigit():
        ts = int(raw)
        if ts > 10_000_000_000:
            ts //= 1000
        return ts
    parsed = _parse_iso_ts(raw)
    return parsed if parsed is not None else -1


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


def _extract_size_usd(raw: dict[str, Any], price: float) -> float:
    direct = _safe_float(raw.get("size_usd"), -1.0)
    if direct >= 0.0:
        return direct
    size = _safe_float(
        raw.get("size", raw.get("quantity", raw.get("amount", raw.get("shares", 0.0)))),
        0.0,
    )
    if size <= 0.0:
        return 0.0
    return size if price <= 0.0 else size * price


def _top_level_price(levels: Any) -> tuple[float, float]:
    if isinstance(levels, list) and levels:
        first = levels[0]
        if isinstance(first, dict):
            price = _safe_float(first.get("price"), -1.0)
            return price, _extract_size_usd(first, price=max(price, 0.0))
    return -1.0, 0.0


def _normalize_orderbook_snapshots(
    raw_snapshots: Any,
    history: list[tuple[int, float]],
    backtest_params: BacktestParams,
) -> tuple[dict[int, OrderBookSnapshot], str]:
    snapshots: dict[int, OrderBookSnapshot] = {}
    if isinstance(raw_snapshots, list):
        for item in raw_snapshots:
            if not isinstance(item, dict):
                continue
            ts = _safe_int(item.get("t"), -1)
            if ts < 0:
                continue
            best_bid = _safe_float(item.get("best_bid"), -1.0)
            best_ask = _safe_float(item.get("best_ask"), -1.0)
            bid_size_usd = _safe_float(item.get("bid_size_usd"), -1.0)
            ask_size_usd = _safe_float(item.get("ask_size_usd"), -1.0)
            if best_bid < 0.0:
                best_bid, inferred_bid_size = _top_level_price(item.get("bids"))
                if bid_size_usd < 0.0:
                    bid_size_usd = inferred_bid_size
            if best_ask < 0.0:
                best_ask, inferred_ask_size = _top_level_price(item.get("asks"))
                if ask_size_usd < 0.0:
                    ask_size_usd = inferred_ask_size
            if best_bid < 0.0 or best_ask < 0.0 or best_bid > best_ask:
                continue
            snapshots[ts] = OrderBookSnapshot(
                t=ts,
                best_bid=best_bid,
                best_ask=best_ask,
                bid_size_usd=max(0.0, bid_size_usd),
                ask_size_usd=max(0.0, ask_size_usd),
            )
    if snapshots:
        return snapshots, "historical"

    if backtest_params.require_orderbook_history:
        raise RuntimeError(
            "Stateful backtest requires historical order-book snapshots. "
            "Provide orderbooks in --backtest-file / backtest_markets or disable require_orderbook_history."
        )

    synthetic: dict[int, OrderBookSnapshot] = {}
    half_spread = backtest_params.synthetic_orderbook_half_spread_bps / 10000.0
    for ts, mid in history:
        synthetic[ts] = OrderBookSnapshot(
            t=ts,
            best_bid=clamp(mid - half_spread, 0.001, 0.999),
            best_ask=clamp(mid + half_spread, 0.001, 0.999),
            bid_size_usd=backtest_params.synthetic_orderbook_depth_usd,
            ask_size_usd=backtest_params.synthetic_orderbook_depth_usd,
        )
    return synthetic, "synthetic"


def _write_telemetry_records(path: str, records: list[dict[str, Any]]) -> None:
    if not path or not records:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")


def _extract_history_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("history", "trades", "data", "items", "results"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return rows
    body = payload.get("body")
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("history", "trades", "data", "items", "results"):
            rows = body.get(key)
            if isinstance(rows, list):
                return rows
    return []


def _normalize_probability(value: Any) -> float:
    p = _safe_float(value, -1.0)
    if 1.0 < p <= 100.0:
        p /= 100.0
    return p


def _row_matches_token(row: dict[str, Any], token_id: str) -> bool:
    token = token_id.strip()
    if not token:
        return True
    observed: list[str] = []
    for key in (
        "token_id",
        "tokenId",
        "tokenID",
        "asset_id",
        "assetId",
        "assetID",
    ):
        raw = _safe_str(row.get(key), "").strip()
        if raw:
            observed.append(raw)
    asset = row.get("asset")
    if isinstance(asset, dict):
        for key in ("id", "token_id", "asset_id"):
            raw = _safe_str(asset.get(key), "").strip()
            if raw:
                observed.append(raw)
    if not observed:
        return True
    return token in observed


def _history_point_from_row(row: Any, token_id: str) -> tuple[int, float] | None:
    if isinstance(row, (list, tuple)) and len(row) >= 2:
        ts = _coerce_unix_ts(row[0])
        p = _normalize_probability(row[1])
        if ts < 0 or not (0.0 <= p <= 1.0):
            return None
        return ts, p

    if not isinstance(row, dict):
        return None
    if not _row_matches_token(row, token_id):
        return None

    ts = -1
    for key in (
        "t",
        "timestamp",
        "ts",
        "time",
        "createdAt",
        "created_at",
        "updatedAt",
        "updated_at",
        "matchTime",
    ):
        ts = _coerce_unix_ts(row.get(key))
        if ts >= 0:
            break
    if ts < 0:
        return None

    p = -1.0
    for key in (
        "p",
        "price",
        "outcomePrice",
        "outcome_price",
        "probability",
        "mid_price",
        "midpoint",
    ):
        candidate = _normalize_probability(row.get(key))
        if 0.0 <= candidate <= 1.0:
            p = candidate
            break
    if p < 0.0:
        return None
    return ts, p


def _is_clob_direct_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc == "clob.polymarket.com"


def _seren_publisher_target(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != SEREN_POLYMARKET_PUBLISHER_HOST:
        raise ValueError(
            f"{POLICY_VIOLATION_BACKTEST_SOURCE}. "
            "Backtest URL must use Seren Polymarket Publisher host "
            f"'https://{SEREN_POLYMARKET_PUBLISHER_HOST}'."
        )
    if not parsed.path.startswith(SEREN_PUBLISHERS_PREFIX):
        raise ValueError(
            f"{POLICY_VIOLATION_BACKTEST_SOURCE}. "
            "Backtest URL must use a supported Seren Polymarket Publisher URL prefix "
            f"('{SEREN_POLYMARKET_DATA_URL_PREFIX}/...')."
        )
    path_without_prefix = parsed.path[len(SEREN_PUBLISHERS_PREFIX) :]
    publisher_slug, _, remainder = path_without_prefix.partition("/")
    if publisher_slug not in SEREN_ALLOWED_POLYMARKET_PUBLISHERS:
        raise ValueError(
            f"{POLICY_VIOLATION_BACKTEST_SOURCE}. "
            "Backtest URL must use a supported Polymarket publisher "
            f"({', '.join(sorted(SEREN_ALLOWED_POLYMARKET_PUBLISHERS))})."
        )
    publisher_path = f"/{remainder}" if remainder else "/"
    if parsed.query:
        publisher_path = f"{publisher_path}?{parsed.query}"
    return publisher_slug, publisher_path


def _runtime_api_key() -> str:
    for env_name in ("API_KEY", "SEREN_API_KEY"):
        token = _safe_str(os.getenv(env_name), "").strip()
        if token:
            return token
    return ""


def _unwrap_seren_response(data: dict[str, Any] | list[Any]) -> dict[str, Any] | list[Any]:
    """Unwrap Seren gateway response envelope {status, body, ...} -> body."""
    if isinstance(data, dict) and "body" in data and "status" in data:
        return data["body"]
    return data


def _http_get_json_via_api_key(url: str, api_key: str, timeout: int = 30) -> dict[str, Any] | list[Any]:
    request = Request(
        url,
        headers={
            "User-Agent": "seren-maker-rebate-bot/1.0",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8"))
        return _unwrap_seren_response(raw)


def _http_get_json_public(url: str, timeout: int = 30) -> dict[str, Any] | list[Any]:
    request = Request(
        url,
        headers={
            "User-Agent": "seren-maker-rebate-bot/1.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8"))
        return _unwrap_seren_response(raw)


def _http_get_json(url: str, timeout: int = 30) -> dict[str, Any] | list[Any]:
    if _is_clob_direct_url(url):
        return _http_get_json_public(url, timeout=timeout)

    _seren_publisher_target(url)

    api_key = _runtime_api_key()
    if not api_key:
        raise RuntimeError(MISSING_RUNTIME_AUTH_ERROR)
    return _http_get_json_via_api_key(url, api_key=api_key, timeout=timeout)


def _http_post_json(url: str, body: dict[str, Any], timeout: int = 30) -> dict[str, Any] | list[Any]:
    """POST JSON to a Seren publisher endpoint (authenticated)."""
    api_key = _runtime_api_key()
    if not api_key:
        raise RuntimeError(MISSING_RUNTIME_AUTH_ERROR)
    data = json.dumps(body).encode("utf-8")
    request = Request(
        url,
        data=data,
        method="POST",
        headers={
            "User-Agent": "seren-maker-rebate-bot/1.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8"))
        return _unwrap_seren_response(raw)


def _check_serenbucks_balance(api_key: str) -> float:
    """Check SerenBucks balance. Returns balance in USD or 0.0 on error."""
    try:
        request = Request(
            "https://api.serendb.com/wallet/balance",
            headers={
                "User-Agent": "seren-maker-rebate-bot/1.0",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            sb = data.get("data") or data.get("serenbucks") or {}
            raw = sb.get("balance_usd") or sb.get("funded_balance_usd") or "0"
            return _safe_float(str(raw).replace("$", "").replace(",", ""), 0.0)
    except Exception as exc:
        print(f"WARNING: could not fetch SerenBucks balance: {exc}", file=sys.stderr)
        return 0.0


def _fetch_predictions_signals(
    market_ids: list[str],
    backtest_params: "BacktestParams",
) -> dict[str, dict[str, Any]]:
    """Fetch cross-platform consensus and divergence signals from Seren Predictions.

    Returns a dict of market_id -> {consensus_prob, divergence_bps, direction, confidence}.
    Costs SerenBucks per batch call. Gracefully returns empty dict on any failure.
    """
    if not backtest_params.predictions_enabled or not market_ids:
        return {}

    api_key = _runtime_api_key()
    if not api_key:
        return {}

    # Check SerenBucks balance first — warn if insufficient
    balance = _check_serenbucks_balance(api_key)
    estimated_cost = 0.30  # batch consensus ($0.15) + batch divergence ($0.15)
    if balance < estimated_cost:
        import sys
        print(
            f"WARNING: SerenBucks balance (${balance:.2f}) may be insufficient for "
            f"predictions intelligence (estimated ${estimated_cost:.2f}). "
            "Buy SerenBucks at https://serendb.com/serenbucks or https://console.serendb.com. "
            "Stripe deposits start at $5, require a verified email, and API-first funding is available via POST /wallet/deposit.",
            file=sys.stderr,
        )
        if balance <= 0.0:
            return {}

    signals: dict[str, dict[str, Any]] = {}

    # Fetch batch divergence — tells us which markets are mispriced vs cross-platform consensus
    try:
        divergence_data = _http_post_json(
            backtest_params.predictions_divergence_url,
            body={"polymarket_ids": market_ids},
            timeout=30,
        )
        if isinstance(divergence_data, dict):
            for market in divergence_data.get("markets", []):
                mid = _safe_str(market.get("polymarket_id"), "")
                if mid:
                    signals[mid] = {
                        "consensus_prob": _safe_float(market.get("consensus_probability"), 0.0),
                        "polymarket_prob": _safe_float(market.get("polymarket_probability"), 0.0),
                        "divergence_bps": _safe_float(market.get("divergence_bps"), 0.0),
                        "direction": _safe_str(market.get("direction"), "neutral"),
                        "confidence": _safe_float(market.get("confidence"), 0.0),
                        "platforms_matched": _safe_int(market.get("platforms_matched"), 0),
                    }
    except Exception:
        pass

    # Fetch batch consensus as fallback if divergence didn't populate
    if not signals:
        try:
            consensus_data = _http_post_json(
                backtest_params.predictions_consensus_url,
                body={"polymarket_ids": market_ids},
                timeout=30,
            )
            if isinstance(consensus_data, dict):
                for market in consensus_data.get("results", []):
                    mid = _safe_str(market.get("polymarket_id"), "")
                    if mid:
                        consensus_prob = _safe_float(market.get("consensus_probability"), 0.0)
                        poly_prob = _safe_float(market.get("polymarket_probability"), 0.0)
                        div_bps = abs(consensus_prob - poly_prob) * 10000.0
                        direction = "buy" if consensus_prob > poly_prob else "sell" if consensus_prob < poly_prob else "neutral"
                        signals[mid] = {
                            "consensus_prob": consensus_prob,
                            "polymarket_prob": poly_prob,
                            "divergence_bps": div_bps,
                            "direction": direction,
                            "confidence": _safe_float(market.get("confidence"), 0.0),
                            "platforms_matched": _safe_int(market.get("platforms_matched"), 0),
                        }
        except Exception:
            pass

    return signals


def _normalize_history(
    history_payload: Any,
    start_ts: int,
    end_ts: int,
    *,
    token_id: str = "",
    fidelity_minutes: int = 1,
) -> list[tuple[int, float]]:
    points: list[tuple[int, float]] = []
    fallback_points: list[tuple[int, float]] = []
    seen: set[int] = set()
    fallback_seen: set[int] = set()

    for row in _extract_history_rows(history_payload):
        parsed = _history_point_from_row(row, token_id=token_id)
        if parsed is None:
            continue
        t, p = parsed
        if t in fallback_seen:
            continue
        fallback_seen.add(t)
        fallback_points.append((t, p))
        if t < start_ts or t > end_ts or t in seen:
            continue
        seen.add(t)
        points.append((t, p))

    points.sort(key=lambda pair: pair[0])
    if not points:
        fallback_points.sort(key=lambda pair: pair[0])
        points = fallback_points

    if not points:
        return []
    if fidelity_minutes <= 1:
        return points

    bucket_seconds = max(60, fidelity_minutes * 60)
    bucketed: dict[int, tuple[int, float]] = {}
    for t, p in points:
        bucketed[t // bucket_seconds] = (t, p)
    return sorted(bucketed.values(), key=lambda pair: pair[0])


def _fetch_market_history(
    backtest_params: BacktestParams,
    token_id: str,
    start_ts: int,
    end_ts: int,
) -> list[tuple[int, float]]:
    history_limit = max(backtest_params.min_history_points * 12, 1000)
    fidelity = backtest_params.fidelity_minutes
    queries = (
        {"market": token_id, "interval": "max", "fidelity": fidelity},
        {"market": token_id, "limit": history_limit},
        {"asset_id": token_id, "limit": history_limit},
        {"token_id": token_id, "limit": history_limit},
    )
    best: list[tuple[int, float]] = []
    for params in queries:
        try:
            payload = _http_get_json(f"{backtest_params.clob_history_url}?{urlencode(params)}")
        except Exception:
            continue
        history = _normalize_history(
            history_payload=payload,
            start_ts=start_ts,
            end_ts=end_ts,
            token_id=token_id,
            fidelity_minutes=backtest_params.fidelity_minutes,
        )
        if len(history) > len(best):
            best = history
        if len(best) >= backtest_params.min_history_points:
            return best
    return best


def _load_markets_from_fixture(
    payload: dict[str, Any] | list[Any],
    start_ts: int,
    end_ts: int,
    backtest_params: BacktestParams,
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
            history_payload=raw.get("history"),
            start_ts=start_ts,
            end_ts=end_ts,
        )
        if len(history) < 2:
            continue
        orderbooks, orderbook_mode = _normalize_orderbook_snapshots(
            raw_snapshots=raw.get("orderbooks", raw.get("book_history")),
            history=history,
            backtest_params=backtest_params,
        )
        market_id = _safe_str(raw.get("market_id"), _safe_str(raw.get("token_id"), "unknown"))
        markets.append(
            {
                "market_id": market_id,
                "question": _safe_str(raw.get("question"), market_id),
                "token_id": _safe_str(raw.get("token_id"), market_id),
                "end_ts": _safe_int(raw.get("end_ts"), _parse_iso_ts(raw.get("endDate")) or 0),
                "rebate_bps": _safe_float(raw.get("rebate_bps"), 0.0),
                "history": history,
                "orderbooks": orderbooks,
                "orderbook_mode": orderbook_mode,
                "source": "fixture",
            }
        )
    return markets


def _snapshot_from_live_book(
    payload: dict[str, Any] | list[Any] | None,
    history: list[tuple[int, float]],
    backtest_params: BacktestParams,
) -> dict[int, OrderBookSnapshot]:
    if not history:
        return {}
    best_bid = -1.0
    best_ask = -1.0
    bid_size = 0.0
    ask_size = 0.0
    if isinstance(payload, dict):
        best_bid = _safe_float(payload.get("best_bid", payload.get("bid")), -1.0)
        best_ask = _safe_float(payload.get("best_ask", payload.get("ask")), -1.0)
        bid_size = _safe_float(payload.get("bid_size_usd"), -1.0)
        ask_size = _safe_float(payload.get("ask_size_usd"), -1.0)
        if best_bid < 0.0:
            best_bid, inferred_bid_size = _top_level_price(payload.get("bids"))
            if bid_size < 0.0:
                bid_size = inferred_bid_size
        if best_ask < 0.0:
            best_ask, inferred_ask_size = _top_level_price(payload.get("asks"))
            if ask_size < 0.0:
                ask_size = inferred_ask_size
    if best_bid < 0.0 or best_ask < 0.0 or best_bid >= best_ask:
        synthetic, _ = _normalize_orderbook_snapshots([], history, backtest_params)
        return synthetic

    half_spread = max(
        (best_ask - best_bid) / 2.0,
        backtest_params.synthetic_orderbook_half_spread_bps / 10000.0,
    )
    reference_bid_size = max(0.0, bid_size) or backtest_params.synthetic_orderbook_depth_usd
    reference_ask_size = max(0.0, ask_size) or backtest_params.synthetic_orderbook_depth_usd
    snapshots: dict[int, OrderBookSnapshot] = {}
    for ts, mid in history:
        snapshots[ts] = OrderBookSnapshot(
            t=ts,
            best_bid=clamp(mid - half_spread, 0.001, 0.999),
            best_ask=clamp(mid + half_spread, 0.001, 0.999),
            bid_size_usd=reference_bid_size,
            ask_size_usd=reference_ask_size,
        )
    return snapshots


def _fetch_live_markets(
    strategy_params: StrategyParams,
    backtest_params: BacktestParams,
    start_ts: int,
    end_ts: int,
) -> list[dict[str, Any]]:
    if backtest_params.require_orderbook_history:
        raise RuntimeError(
            "Historical order-book replay is required. Provide --backtest-file or backtest_markets "
            "with orderbooks because live publisher fetch does not supply historical book snapshots."
        )
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
        # Parse mid-price from outcomePrices for ranking
        outcome_prices = _json_to_list(market.get("outcomePrices"))
        mid_price = _safe_float(outcome_prices[0] if outcome_prices else None, 0.5)
        spread = _safe_float(market.get("spread"), 1.0)
        volume24hr = _safe_float(market.get("volume24hr"), 0.0)
        # Score: prefer price near 0.5 (two-way flow), high volume, tight spread
        price_score = 1.0 - abs(mid_price - 0.5) * 2.0  # 1.0 at 0.50, 0.0 at 0.0/1.0
        spread_score = max(0.0, 1.0 - spread * 10.0)     # penalise wide spreads
        volume_score = min(1.0, volume24hr / 50000.0)     # saturates at $50K/day
        mm_score = price_score * 0.4 + volume_score * 0.4 + spread_score * 0.2
        candidates.append(
            {
                "market_id": _safe_str(market.get("id"), token_id),
                "question": _safe_str(market.get("question"), token_id),
                "token_id": token_id,
                "end_ts": end_market,
                "rebate_bps": _safe_float(market.get("rebate_bps"), 0.0),
                "volume24hr": volume24hr,
                "mm_score": mm_score,
            }
        )

    # Rank by market-making quality score, not arbitrary API order
    candidates.sort(key=lambda c: c["mm_score"], reverse=True)

    # Fetch history for ALL candidates concurrently, then rank and pick best N
    def _enrich_candidate(candidate: dict[str, Any]) -> dict[str, Any] | None:
        history = _fetch_market_history(
            backtest_params=backtest_params,
            token_id=candidate["token_id"],
            start_ts=start_ts,
            end_ts=end_ts,
        )
        if len(history) < backtest_params.min_history_points:
            return None
        try:
            book_payload = _http_get_json_public(
                f"{POLYMARKET_CLOB_BASE_URL}/book?{urlencode({'token_id': candidate['token_id']})}"
            )
        except Exception:
            book_payload = None
        orderbooks = _snapshot_from_live_book(
            payload=book_payload,
            history=history,
            backtest_params=backtest_params,
        )
        return {
            **candidate,
            "history": history,
            "orderbooks": orderbooks,
            "orderbook_mode": "synthetic-from-live-book",
            "source": "live-seren-publisher",
        }

    enriched: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(_enrich_candidate, c): c for c in candidates}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                enriched.append(result)

    # Fetch Seren Predictions intelligence to boost scoring (costs SerenBucks)
    predictions = _fetch_predictions_signals(
        market_ids=[c["market_id"] for c in enriched],
        backtest_params=backtest_params,
    )
    if predictions:
        for candidate in enriched:
            signal = predictions.get(candidate["market_id"])
            if signal and signal.get("divergence_bps", 0) > 0:
                # Boost mm_score for markets with cross-platform divergence (= edge)
                divergence_factor = min(1.0, signal["divergence_bps"] / 500.0)
                candidate["mm_score"] = candidate.get("mm_score", 0.0) + (
                    backtest_params.predictions_score_boost * divergence_factor
                )
                candidate["prediction_signal"] = signal

    # Re-sort enriched candidates by mm_score and return top N
    enriched.sort(key=lambda c: c.get("mm_score", 0.0), reverse=True)
    return enriched[: strategy_params.markets_max]


def _fetch_live_quote_markets(config: dict[str, Any]) -> list[dict[str, Any]]:
    strategy_params = to_params(config)
    backtest_params = to_backtest_params(config)
    now_ts = int(time.time())
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

    markets: list[dict[str, Any]] = []
    for market in raw:
        if not isinstance(market, dict):
            continue
        liquidity = _safe_float(market.get("liquidity"), 0.0)
        if liquidity < backtest_params.min_liquidity_usd:
            continue

        end_ts = (
            _parse_iso_ts(market.get("endDate"))
            or _parse_iso_ts(market.get("endDateIso"))
            or _safe_int(market.get("end_ts"), 0)
        )
        seconds_to_resolution = max(0, end_ts - now_ts) if end_ts else 0
        if seconds_to_resolution < strategy_params.min_seconds_to_resolution:
            continue

        token_ids = _json_to_list(market.get("clobTokenIds"))
        if not token_ids:
            continue
        token_id = _safe_str(token_ids[0], "")
        if not token_id:
            continue

        mid_price = _extract_live_mid_price(market)
        if not (0.01 < mid_price < 0.99):
            continue

        best_bid, best_ask = _extract_live_book(market, mid_price)
        volatility_bps = max(abs(best_ask - best_bid) * 10000.0, strategy_params.min_spread_bps)
        market_id = _safe_str(market.get("id"), _safe_str(market.get("conditionId"), token_id))
        markets.append(
            {
                "market_id": market_id,
                "question": _safe_str(market.get("question"), market_id),
                "token_id": token_id,
                "mid_price": round(mid_price, 6),
                "best_bid": round(best_bid, 6),
                "best_ask": round(best_ask, 6),
                "seconds_to_resolution": seconds_to_resolution,
                "volatility_bps": round(volatility_bps, 3),
                "rebate_bps": _safe_float(market.get("rebate_bps"), strategy_params.default_rebate_bps),
                "source": "live-seren-publisher",
            }
        )
        if len(markets) >= strategy_params.markets_max:
            break

    return markets


def _max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0
    bankroll = equity_curve[0]
    peak = float("-inf")
    max_dd = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        max_dd = max(max_dd, peak - value)
    return min(max_dd, bankroll)


def _build_quote_plan(
    *,
    market_id: str,
    mid_price: float,
    volatility_bps: float,
    rebate_bps: float,
    inventory_notional: float,
    outstanding_notional: float,
    strategy_params: StrategyParams,
    prediction_skew_bps: float = 0.0,
) -> QuotePlan:
    spread_bps = compute_spread_bps(volatility_bps, strategy_params)
    edge_bps = expected_edge_bps(spread_bps, rebate_bps, strategy_params)
    # Predictions intelligence adds directional edge
    effective_edge = edge_bps + abs(prediction_skew_bps) * 0.5
    if effective_edge < strategy_params.min_edge_bps:
        return QuotePlan(
            status="skipped",
            market_id=market_id,
            reason="negative_or_thin_edge",
            edge_bps=round(edge_bps, 3),
            spread_bps=round(spread_bps, 3),
            rebate_bps=round(rebate_bps, 3),
            inventory_notional_usd=round(inventory_notional, 2),
        )

    inventory_ratio = 0.0
    if strategy_params.max_position_notional_usd > 0:
        inventory_ratio = clamp(
            inventory_notional / strategy_params.max_position_notional_usd,
            -1.0,
            1.0,
        )
    # Combine inventory skew with predictions directional skew
    skew_bps = -inventory_ratio * strategy_params.inventory_skew_strength_bps + prediction_skew_bps
    half_spread_prob = (spread_bps / 2.0) / 10000.0
    skew_prob = skew_bps / 10000.0
    bid_price = clamp(mid_price - half_spread_prob + skew_prob, 0.001, 0.999)
    ask_price = clamp(mid_price + half_spread_prob + skew_prob, 0.001, 0.999)
    if bid_price >= ask_price:
        return QuotePlan(
            status="skipped",
            market_id=market_id,
            reason="crossed_quote_after_skew",
            edge_bps=round(edge_bps, 3),
            spread_bps=round(spread_bps, 3),
            rebate_bps=round(rebate_bps, 3),
            inventory_notional_usd=round(inventory_notional, 2),
        )

    remaining_market = max(0.0, strategy_params.max_notional_per_market_usd - abs(inventory_notional))
    remaining_total = max(0.0, strategy_params.max_total_notional_usd - max(0.0, outstanding_notional))
    bid_position_capacity = max(0.0, strategy_params.max_position_notional_usd - inventory_notional)
    ask_position_capacity = max(0.0, strategy_params.max_position_notional_usd + inventory_notional)
    per_side_market_budget = remaining_market / 2.0
    per_side_total_budget = remaining_total / 2.0
    bid_notional = min(
        strategy_params.base_order_notional_usd,
        per_side_market_budget,
        per_side_total_budget,
        bid_position_capacity,
    )
    ask_notional = min(
        strategy_params.base_order_notional_usd,
        per_side_market_budget,
        per_side_total_budget,
        ask_position_capacity,
    )
    if bid_notional <= 0.0 and ask_notional <= 0.0:
        return QuotePlan(
            status="skipped",
            market_id=market_id,
            reason="risk_capacity_exhausted",
            edge_bps=round(edge_bps, 3),
            spread_bps=round(spread_bps, 3),
            rebate_bps=round(rebate_bps, 3),
            inventory_notional_usd=round(inventory_notional, 2),
        )

    return QuotePlan(
        status="quoted",
        market_id=market_id,
        edge_bps=round(edge_bps, 3),
        spread_bps=round(spread_bps, 3),
        rebate_bps=round(rebate_bps, 3),
        bid_price=round(bid_price, 4),
        ask_price=round(ask_price, 4),
        bid_notional_usd=round(max(0.0, bid_notional), 2),
        ask_notional_usd=round(max(0.0, ask_notional), 2),
        inventory_notional_usd=round(inventory_notional, 2),
    )


def _liquidation_equity(
    *,
    cash_usd: float,
    position_shares: float,
    mark_price: float,
    unwind_cost_bps: float,
) -> float:
    inventory_value = position_shares * mark_price
    liquidation_cost = abs(inventory_value) * unwind_cost_bps / 10000.0
    return cash_usd + inventory_value - liquidation_cost


def _fill_fraction(
    *,
    side: str,
    quote_price: float,
    quote_notional: float,
    current_book: OrderBookSnapshot,
    next_book: OrderBookSnapshot,
    next_mid: float,
    spread_bps: float,
    backtest_params: BacktestParams,
    strategy_params: StrategyParams,
) -> float:
    if quote_notional <= 0.0:
        return 0.0
    if side == "buy":
        touched_price = min(next_mid, next_book.best_bid)
        touched_distance_bps = max(0.0, (quote_price - touched_price) * 10000.0)
        displayed_size = next_book.ask_size_usd
        queue_factor = (
            backtest_params.join_best_queue_factor
            if quote_price >= current_book.best_bid
            else backtest_params.off_best_queue_factor
        )
    else:
        touched_price = max(next_mid, next_book.best_ask)
        touched_distance_bps = max(0.0, (touched_price - quote_price) * 10000.0)
        displayed_size = next_book.bid_size_usd
        queue_factor = (
            backtest_params.join_best_queue_factor
            if quote_price <= current_book.best_ask
            else backtest_params.off_best_queue_factor
        )
    if touched_distance_bps <= 0.0:
        return 0.0
    half_spread_bps = max(spread_bps / 2.0, 1.0)
    touch_ratio = clamp(touched_distance_bps / half_spread_bps, 0.0, 1.0)
    spread_decay = math.exp(
        -max(0.0, spread_bps - strategy_params.min_spread_bps) / backtest_params.spread_decay_bps
    )
    depth_factor = clamp(displayed_size / max(quote_notional, 1e-9), 0.0, 1.0)
    return clamp(
        backtest_params.participation_rate * touch_ratio * spread_decay * queue_factor * depth_factor,
        0.0,
        1.0,
    )


def _apply_fill(
    *,
    side: str,
    fill_notional: float,
    fill_price: float,
    rebate_bps: float,
    cash_usd: float,
    position_shares: float,
) -> tuple[float, float]:
    shares = fill_notional / max(fill_price, 0.01)
    if side == "buy":
        cash_usd -= shares * fill_price
        position_shares += shares
    else:
        cash_usd += shares * fill_price
        position_shares -= shares
    cash_usd += fill_notional * rebate_bps / 10000.0
    return cash_usd, position_shares


def _simulate_market_backtest(
    market: dict[str, Any],
    strategy_params: StrategyParams,
    backtest_params: BacktestParams,
    allocated_capital: float = 0.0,
) -> dict[str, Any]:
    capital = allocated_capital if allocated_capital > 0.0 else strategy_params.bankroll_usd
    history: list[tuple[int, float]] = market["history"]
    orderbooks: dict[int, OrderBookSnapshot] = market.get("orderbooks", {})
    window = backtest_params.volatility_window_points
    if len(history) < window + 2:
        return {
            "market_id": market["market_id"],
            "question": market["question"],
            "considered_points": 0,
            "quoted_points": 0,
            "skipped_points": 0,
            "fill_events": 0,
            "filled_notional_usd": 0.0,
            "pnl_usd": 0.0,
            "equity_curve": [capital],
            "telemetry": [],
            "orderbook_mode": market.get("orderbook_mode", "unknown"),
        }

    rebate_bps = _safe_float(market.get("rebate_bps"), strategy_params.default_rebate_bps)
    if rebate_bps <= 0:
        rebate_bps = strategy_params.default_rebate_bps
    end_ts = _safe_int(market.get("end_ts"), 0)
    moves_bps = [abs((history[i][1] - history[i - 1][1]) * 10000.0) for i in range(1, len(history))]

    # Compute prediction-based directional skew (positive = lean bid/buy, negative = lean ask/sell)
    prediction_skew_bps = 0.0
    signal = market.get("prediction_signal")
    if signal and backtest_params.predictions_enabled:
        confidence = _safe_float(signal.get("confidence"), 0.0)
        divergence = _safe_float(signal.get("divergence_bps"), 0.0)
        direction = _safe_str(signal.get("direction"), "neutral")
        if direction != "neutral" and confidence > 0.0 and divergence > 0.0:
            strength = min(1.0, divergence / 500.0) * min(1.0, confidence)
            prediction_skew_bps = backtest_params.predictions_skew_strength_bps * strength
            if direction == "sell":
                prediction_skew_bps = -prediction_skew_bps

    cash_usd = capital
    position_shares = 0.0
    considered = 0
    quoted = 0
    skipped = 0
    fill_events = 0
    filled_notional = 0.0
    telemetry: list[dict[str, Any]] = []
    equity_curve = [capital]

    for i in range(window, len(history) - 1):
        t, mid_price = history[i]
        next_t, next_price = history[i + 1]
        current_book = orderbooks.get(t)
        next_book = orderbooks.get(next_t, current_book)
        if current_book is None or next_book is None:
            skipped += 1
            continue

        considered += 1
        record: dict[str, Any] = {
            "t": t,
            "market_id": market["market_id"],
            "mid_price": round(mid_price, 6),
            "next_mid_price": round(next_price, 6),
            "best_bid": round(current_book.best_bid, 6),
            "best_ask": round(current_book.best_ask, 6),
            "inventory_notional_before_usd": round(position_shares * mid_price, 6),
            "orderbook_mode": market.get("orderbook_mode", "unknown"),
        }
        if end_ts and end_ts - t < strategy_params.min_seconds_to_resolution:
            skipped += 1
            record["status"] = "skipped"
            record["reason"] = "near_resolution"
            telemetry.append(record)
            continue
        if mid_price <= 0.01 or mid_price >= 0.99:
            skipped += 1
            record["status"] = "skipped"
            record["reason"] = "extreme_probability"
            telemetry.append(record)
            continue

        vol_slice = moves_bps[i - window : i]
        vol_bps = pstdev(vol_slice) if len(vol_slice) > 1 else strategy_params.min_spread_bps
        quote_plan = _build_quote_plan(
            market_id=_safe_str(market.get("market_id"), "unknown"),
            mid_price=mid_price,
            volatility_bps=vol_bps,
            rebate_bps=rebate_bps,
            inventory_notional=position_shares * mid_price,
            outstanding_notional=abs(position_shares * mid_price),
            strategy_params=strategy_params,
            prediction_skew_bps=prediction_skew_bps,
        )
        record.update(
            {
                "status": quote_plan.status,
                "reason": quote_plan.reason,
                "spread_bps": quote_plan.spread_bps,
                "edge_bps": quote_plan.edge_bps,
                "bid_price": quote_plan.bid_price,
                "ask_price": quote_plan.ask_price,
                "bid_notional_usd": quote_plan.bid_notional_usd,
                "ask_notional_usd": quote_plan.ask_notional_usd,
            }
        )
        if quote_plan.status != "quoted":
            skipped += 1
            telemetry.append(record)
            equity_curve.append(
                max(0.0, _liquidation_equity(
                    cash_usd=cash_usd,
                    position_shares=position_shares,
                    mark_price=next_price,
                    unwind_cost_bps=strategy_params.expected_unwind_cost_bps,
                ))
            )
            continue

        quoted += 1
        side: str | None = None
        if next_price < mid_price and quote_plan.bid_notional_usd > 0.0:
            side = "buy"
        elif next_price > mid_price and quote_plan.ask_notional_usd > 0.0:
            side = "sell"

        previous_equity = _liquidation_equity(
            cash_usd=cash_usd,
            position_shares=position_shares,
            mark_price=mid_price,
            unwind_cost_bps=strategy_params.expected_unwind_cost_bps,
        )
        fill_fraction = 0.0
        fill_notional = 0.0
        fill_price = 0.0
        if side == "buy":
            fill_fraction = _fill_fraction(
                side="buy",
                quote_price=quote_plan.bid_price,
                quote_notional=quote_plan.bid_notional_usd,
                current_book=current_book,
                next_book=next_book,
                next_mid=next_price,
                spread_bps=quote_plan.spread_bps,
                backtest_params=backtest_params,
                strategy_params=strategy_params,
            )
            fill_notional = quote_plan.bid_notional_usd * fill_fraction
            fill_price = quote_plan.bid_price
        elif side == "sell":
            fill_fraction = _fill_fraction(
                side="sell",
                quote_price=quote_plan.ask_price,
                quote_notional=quote_plan.ask_notional_usd,
                current_book=current_book,
                next_book=next_book,
                next_mid=next_price,
                spread_bps=quote_plan.spread_bps,
                backtest_params=backtest_params,
                strategy_params=strategy_params,
            )
            fill_notional = quote_plan.ask_notional_usd * fill_fraction
            fill_price = quote_plan.ask_price

        if fill_notional > 0.0 and side is not None:
            cash_usd, position_shares = _apply_fill(
                side=side,
                fill_notional=fill_notional,
                fill_price=fill_price,
                rebate_bps=rebate_bps,
                cash_usd=cash_usd,
                position_shares=position_shares,
            )
            filled_notional += fill_notional
            fill_events += 1

        equity_after = _liquidation_equity(
            cash_usd=cash_usd,
            position_shares=position_shares,
            mark_price=next_price,
            unwind_cost_bps=strategy_params.expected_unwind_cost_bps,
        )
        equity_after = max(0.0, equity_after)
        equity_curve.append(equity_after)
        record.update(
            {
                "fill_side": side or "",
                "fill_fraction": round(fill_fraction, 6),
                "fill_notional_usd": round(fill_notional, 6),
                "inventory_notional_after_usd": round(position_shares * next_price, 6),
                "equity_before_usd": round(previous_equity, 6),
                "equity_after_usd": round(equity_after, 6),
                "event_pnl_usd": round(equity_after - previous_equity, 6),
            }
        )
        telemetry.append(record)
        if equity_after <= 0.0:
            break

    ending_equity = max(0.0, _liquidation_equity(
        cash_usd=cash_usd,
        position_shares=position_shares,
        mark_price=history[-1][1],
        unwind_cost_bps=strategy_params.expected_unwind_cost_bps,
    ))
    if not equity_curve or ending_equity != equity_curve[-1]:
        equity_curve.append(ending_equity)
    return {
        "market_id": market["market_id"],
        "question": market["question"],
        "considered_points": considered,
        "quoted_points": quoted,
        "skipped_points": skipped,
        "fill_events": fill_events,
        "filled_notional_usd": round(filled_notional, 4),
        "pnl_usd": round(ending_equity - capital, 6),
        "equity_curve": equity_curve,
        "telemetry": telemetry,
        "orderbook_mode": market.get("orderbook_mode", "unknown"),
    }


def _market_target_descriptors(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "market_id": _safe_str(market.get("market_id"), "unknown"),
            "question": _safe_str(market.get("question"), ""),
        }
        for market in markets
    ]


def _diff_section(original: dict[str, Any], updated: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key, value in updated.items():
        if original.get(key) != value:
            diff[key] = value
    return diff


def _rank_markets_for_optimization(markets: list[dict[str, Any]], result: dict[str, Any]) -> list[dict[str, Any]]:
    pnl_by_market = {
        _safe_str(row.get("market_id"), "unknown"): _safe_float(row.get("pnl_usd"), 0.0)
        for row in result.get("markets", [])
        if isinstance(row, dict)
    }
    return sorted(
        markets,
        key=lambda market: (
            pnl_by_market.get(_safe_str(market.get("market_id"), "unknown"), float("-inf")),
            _safe_float(market.get("rebate_bps"), 0.0),
        ),
        reverse=True,
    )


def _optimization_attempt_summary(
    *,
    name: str,
    result: dict[str, Any],
    strategy_updates: dict[str, Any],
    backtest_updates: dict[str, Any],
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "name": name,
        "status": _safe_str(result.get("status"), "error"),
        "return_pct": round(_safe_float(result.get("results", {}).get("return_pct"), 0.0), 4),
        "total_pnl_usd": round(_safe_float(result.get("results", {}).get("total_pnl_usd"), 0.0), 4),
        "target_market_count": len(targets),
        "target_market_ids": [target["market_id"] for target in targets],
        "strategy_updates": strategy_updates,
        "backtest_updates": backtest_updates,
    }


def _is_better_backtest_result(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    if candidate.get("status") != "ok":
        return False
    if current.get("status") != "ok":
        return True
    candidate_return = _safe_float(candidate.get("results", {}).get("return_pct"), float("-inf"))
    current_return = _safe_float(current.get("results", {}).get("return_pct"), float("-inf"))
    if candidate_return != current_return:
        return candidate_return > current_return
    candidate_pnl = _safe_float(candidate.get("results", {}).get("total_pnl_usd"), float("-inf"))
    current_pnl = _safe_float(current.get("results", {}).get("total_pnl_usd"), float("-inf"))
    return candidate_pnl > current_pnl


def _maker_optimization_candidates(config: dict[str, Any], total_markets: int) -> list[dict[str, Any]]:
    p = to_params(config)
    bt = to_backtest_params(config)
    base_markets = max(1, min(total_markets, p.markets_max))
    focus_markets = max(1, min(total_markets, max(1, int(round(base_markets * 0.5)))))
    broad_markets = max(1, min(total_markets, max(base_markets, int(round(total_markets * 0.75)))))
    return [
        {
            "name": "focus-higher-participation",
            "subset_size": focus_markets,
            "strategy": {
                "markets_max": focus_markets,
                "base_order_notional_usd": round(p.base_order_notional_usd * 1.15, 4),
                "max_total_notional_usd": round(p.max_total_notional_usd * 1.1, 4),
            },
            "backtest": {"participation_rate": round(clamp(bt.participation_rate + 0.15, 0.0, 1.0), 4)},
        },
        {
            "name": "focus-tighter-spread",
            "subset_size": focus_markets,
            "strategy": {
                "markets_max": focus_markets,
                "min_spread_bps": round(max(5.0, p.min_spread_bps * 0.85), 4),
                "max_spread_bps": round(max(p.min_spread_bps, p.max_spread_bps * 0.9), 4),
                "base_order_notional_usd": round(p.base_order_notional_usd * 1.1, 4),
            },
            "backtest": {"participation_rate": round(clamp(bt.participation_rate + 0.1, 0.0, 1.0), 4)},
        },
        {
            "name": "high-conviction",
            "subset_size": focus_markets,
            "strategy": {
                "markets_max": focus_markets,
                "min_edge_bps": round(max(0.5, p.min_edge_bps * 0.75), 4),
                "base_order_notional_usd": round(p.base_order_notional_usd * 1.25, 4),
                "max_notional_per_market_usd": round(p.max_notional_per_market_usd * 1.2, 4),
                "max_total_notional_usd": round(p.max_total_notional_usd * 1.15, 4),
                "max_position_notional_usd": round(p.max_position_notional_usd * 1.15, 4),
            },
            "backtest": {"participation_rate": round(clamp(bt.participation_rate + 0.2, 0.0, 1.0), 4)},
        },
        {
            "name": "broader-scan",
            "subset_size": broad_markets,
            "strategy": {
                "markets_max": broad_markets,
                "min_spread_bps": round(max(5.0, p.min_spread_bps * 0.9), 4),
                "base_order_notional_usd": round(p.base_order_notional_usd * 1.05, 4),
            },
            "backtest": {"participation_rate": round(clamp(bt.participation_rate + 0.05, 0.0, 1.0), 4)},
        },
    ]


def _evaluate_backtest(
    *,
    config: dict[str, Any],
    markets: list[dict[str, Any]],
    source: str,
    days: int,
    start_ts: int,
    end_ts: int,
) -> dict[str, Any]:
    strategy_params = to_params(config)
    backtest_params = to_backtest_params(config)
    strategy_params = replace(strategy_params, bankroll_usd=backtest_params.bankroll_usd)
    market_summaries: list[dict[str, Any]] = []
    equity_curve = [strategy_params.bankroll_usd]
    total_considered = 0
    total_quoted = 0
    total_notional = 0.0
    total_fill_events = 0
    telemetry_records: list[dict[str, Any]] = []
    orderbook_modes: set[str] = set()

    selected_markets = markets[: strategy_params.markets_max]
    capital_per_market = strategy_params.bankroll_usd / max(1, len(selected_markets))

    for market in selected_markets:
        summary = _simulate_market_backtest(
            market=market,
            strategy_params=strategy_params,
            backtest_params=backtest_params,
            allocated_capital=capital_per_market,
        )
        market_summaries.append(
            {
                "market_id": summary["market_id"],
                "question": summary["question"],
                "considered_points": summary["considered_points"],
                "quoted_points": summary["quoted_points"],
                "skipped_points": summary["skipped_points"],
                "fill_events": summary["fill_events"],
                "filled_notional_usd": summary["filled_notional_usd"],
                "pnl_usd": summary["pnl_usd"],
                "orderbook_mode": summary["orderbook_mode"],
            }
        )
        total_considered += int(summary["considered_points"])
        total_quoted += int(summary["quoted_points"])
        total_notional += float(summary["filled_notional_usd"])
        total_fill_events += int(summary["fill_events"])
        telemetry_records.extend(summary["telemetry"])
        orderbook_modes.add(_safe_str(summary.get("orderbook_mode"), "unknown"))

        market_equity_curve = summary["equity_curve"]
        if len(market_equity_curve) > len(equity_curve):
            equity_curve.extend([equity_curve[-1]] * (len(market_equity_curve) - len(equity_curve)))
        for idx, value in enumerate(market_equity_curve):
            if idx < len(equity_curve):
                equity_curve[idx] += value - capital_per_market

    ending_equity = equity_curve[-1]
    total_pnl = ending_equity - strategy_params.bankroll_usd
    return_pct = (total_pnl / strategy_params.bankroll_usd) * 100.0
    max_drawdown = _max_drawdown(equity_curve)
    decision = "consider_live_guarded" if total_pnl > 0 else "paper_only_or_tune"
    _write_telemetry_records(backtest_params.telemetry_path, telemetry_records)

    return {
        "status": "ok",
        "skill": "polymarket-maker-rebate-bot",
        "mode": "backtest",
        "dry_run": True,
        "predictions_intelligence": {
            "enabled": backtest_params.predictions_enabled,
            "markets_with_signals": sum(1 for m in selected_markets if m.get("prediction_signal")),
            "skew_strength_bps": backtest_params.predictions_skew_strength_bps,
            "score_boost": backtest_params.predictions_score_boost,
            "note": (
                "Seren Predictions intelligence active — costs SerenBucks per batch call."
                if backtest_params.predictions_enabled
                else "Disabled. Set predictions_enabled: true in config to activate (costs SerenBucks)."
            ),
        },
        "backtest_summary": {
            "days": days,
            "source": source,
            "start_utc": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
            "end_utc": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
            "markets_selected": len(market_summaries),
            "considered_points": total_considered,
            "quoted_points": total_quoted,
            "fill_events": total_fill_events,
            "orderbook_mode": ",".join(sorted(orderbook_modes)),
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
            "events": total_fill_events,
            "max_drawdown_usd": round(max_drawdown, 4),
            "telemetry_path": backtest_params.telemetry_path or None,
            "decision_hint": decision,
            "disclaimer": "Backtests are estimates and do not guarantee future performance.",
        },
        "markets": sorted(market_summaries, key=lambda item: item["pnl_usd"], reverse=True),
        **(
            {
                "predictions_upgrade_prompt": {
                    "message": (
                        "Improve your edge with Seren Predictions intelligence. "
                        "Cross-platform consensus and divergence signals from Kalshi, Manifold, "
                        "Metaculus, PredictIt, and Betfair can boost market selection and directional "
                        "quote skew. Estimated cost: ~$0.30 per backtest run."
                    ),
                    "action": 'Set "predictions_enabled": true in your config.json backtest section.',
                    "publisher": "seren-polymarket-intelligence",
                    "estimated_cost_usd": 0.30,
                    "endpoints_used": [
                        "POST /api/oracle/divergence/batch ($0.15)",
                        "POST /api/oracle/consensus/batch ($0.15)",
                    ],
                    "benefits": [
                        "Boost mm_score for markets with cross-platform price divergence",
                        "Add directional skew to quotes based on consensus vs Polymarket price",
                        "Filter for markets where other platforms disagree — higher edge potential",
                    ],
                },
            }
            if not backtest_params.predictions_enabled
            else {}
        ),
        "next_steps": [
            "Review negative-PnL markets and edge assumptions.",
            "Tune spread, participation, and risk caps before live mode.",
            "Run quote mode only after backtest results are acceptable.",
            *(
                []
                if backtest_params.predictions_enabled
                else [
                    "Enable Seren Predictions intelligence for better edge detection "
                    "(set predictions_enabled: true in config).",
                ]
            ),
        ],
    }


def _optimize_backtest(
    *,
    config: dict[str, Any],
    markets: list[dict[str, Any]],
    source: str,
    days: int,
    start_ts: int,
    end_ts: int,
) -> dict[str, Any]:
    baseline = _evaluate_backtest(
        config=config,
        markets=markets,
        source=source,
        days=days,
        start_ts=start_ts,
        end_ts=end_ts,
    )
    if baseline.get("status") != "ok":
        return baseline

    optimization = to_optimization_params(config)
    ranked_markets = _rank_markets_for_optimization(markets, baseline)
    best_result = baseline
    best_config = _clone_config(config)
    best_targets = _market_target_descriptors(ranked_markets[: to_params(config).markets_max])
    attempts = [
        _optimization_attempt_summary(
            name="baseline",
            result=baseline,
            strategy_updates={},
            backtest_updates={},
            targets=best_targets,
        )
    ]

    if optimization.enabled:
        max_attempts = max(1, optimization.max_iterations)
        for candidate in _maker_optimization_candidates(config, len(ranked_markets))[: max(0, max_attempts - 1)]:
            if _safe_float(best_result.get("results", {}).get("return_pct"), 0.0) >= optimization.target_return_pct:
                break
            subset_size = max(1, min(len(ranked_markets), _safe_int(candidate.get("subset_size"), len(ranked_markets))))
            candidate_markets = ranked_markets[:subset_size]
            candidate_config = _clone_config(config)
            candidate_config.setdefault("strategy", {}).update(candidate.get("strategy", {}))
            candidate_config.setdefault("backtest", {}).update(candidate.get("backtest", {}))
            candidate_result = _evaluate_backtest(
                config=candidate_config,
                markets=candidate_markets,
                source=f"{source}|optimized:{candidate['name']}",
                days=days,
                start_ts=start_ts,
                end_ts=end_ts,
            )
            candidate_targets = _market_target_descriptors(candidate_markets[: to_params(candidate_config).markets_max])
            attempts.append(
                _optimization_attempt_summary(
                    name=_safe_str(candidate.get("name"), "candidate"),
                    result=candidate_result,
                    strategy_updates=_diff_section(config.get("strategy", {}), candidate_config.get("strategy", {})),
                    backtest_updates=_diff_section(config.get("backtest", {}), candidate_config.get("backtest", {})),
                    targets=candidate_targets,
                )
            )
            if _is_better_backtest_result(candidate_result, best_result):
                best_result = candidate_result
                best_config = candidate_config
                best_targets = candidate_targets

    strategy_updates = _diff_section(config.get("strategy", {}), best_config.get("strategy", {}))
    backtest_updates = _diff_section(config.get("backtest", {}), best_config.get("backtest", {}))
    best_return_pct = _safe_float(best_result.get("results", {}).get("return_pct"), 0.0)
    optimization_state = {
        "enabled": optimization.enabled,
        "target_return_pct": round(optimization.target_return_pct, 4),
        "target_met": best_return_pct >= optimization.target_return_pct,
        "selected_attempt": next(
            (attempt["name"] for attempt in attempts if attempt["return_pct"] == round(best_return_pct, 4)),
            attempts[0]["name"],
        ),
        "best_return_pct": round(best_return_pct, 4),
        "attempt_count": len(attempts),
        "target_markets": best_targets,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    best_result["optimization_summary"] = {
        **optimization_state,
        "attempts": attempts,
        "strategy_updates": strategy_updates,
        "backtest_updates": backtest_updates,
    }
    best_result["config_updates"] = {
        "strategy": strategy_updates,
        "backtest": backtest_updates,
        "state": {"backtest_optimizer": optimization_state},
    }
    return best_result


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
                backtest_params=backtest_params,
            )
            source = "file"
        elif config.get("backtest_markets"):
            markets = _load_markets_from_fixture(
                payload=config.get("backtest_markets", []),
                start_ts=start_ts,
                end_ts=end_ts,
                backtest_params=backtest_params,
            )
            source = "config"
        else:
            markets = _fetch_live_markets(
                strategy_params=strategy_params,
                backtest_params=backtest_params,
                start_ts=start_ts,
                end_ts=end_ts,
            )
            source = "live-seren-publisher"
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

    return _optimize_backtest(
        config=config,
        markets=markets,
        source=source,
        days=days,
        start_ts=start_ts,
        end_ts=end_ts,
    )


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
    quote_plan = _build_quote_plan(
        market_id=market_id,
        mid_price=mid,
        volatility_bps=vol_bps,
        rebate_bps=rebate_bps,
        inventory_notional=inventory_notional,
        outstanding_notional=outstanding_notional,
        strategy_params=p,
    )
    if quote_plan.status != "quoted":
        return {
            "market_id": market_id,
            "status": "skipped",
            "reason": quote_plan.reason,
            "edge_bps": quote_plan.edge_bps,
        }
    total_notional = quote_plan.bid_notional_usd + quote_plan.ask_notional_usd
    return {
        "market_id": market_id,
        "status": quote_plan.status,
        "edge_bps": quote_plan.edge_bps,
        "spread_bps": quote_plan.spread_bps,
        "rebate_bps": quote_plan.rebate_bps,
        "quote_notional_usd": round(total_notional, 2),
        "bid_notional_usd": quote_plan.bid_notional_usd,
        "ask_notional_usd": quote_plan.ask_notional_usd,
        "bid_price": quote_plan.bid_price,
        "ask_price": quote_plan.ask_price,
        "inventory_notional_usd": quote_plan.inventory_notional_usd,
    }


def run_once(
    config: dict[str, Any],
    markets: list[dict[str, Any]],
    yes_live: bool,
) -> dict[str, Any]:
    params = to_params(config)
    execution = config.get("execution", {}) if isinstance(config.get("execution"), dict) else {}
    backtest_params = to_backtest_params(config)
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

    prefer_live_market_data = bool(execution.get("prefer_live_market_data", live_mode))
    market_source = "config"
    if prefer_live_market_data:
        try:
            live_markets = load_live_single_markets(
                markets_max=params.markets_max,
                min_seconds_to_resolution=params.min_seconds_to_resolution,
                volatility_window_points=backtest_params.volatility_window_points,
                min_history_points=max(24, backtest_params.volatility_window_points * 4),
                min_liquidity_usd=backtest_params.min_liquidity_usd,
                markets_fetch_limit=max(params.markets_max * 5, backtest_params.markets_fetch_limit),
                history_interval="max",
                history_fidelity_minutes=backtest_params.fidelity_minutes,
                default_rebate_bps=params.default_rebate_bps,
                timeout_seconds=30.0,
            )
        except Exception as exc:
            if not markets:
                return {
                    "status": "error",
                    "error_code": "live_market_data_load_failed",
                    "message": str(exc),
                    "dry_run": True,
                }
            live_markets = []
        if live_markets:
            markets = live_markets
            market_source = "live-seren-publisher"

    inventory = config.get("state", {}).get("inventory", {})
    inventory_notional_by_market = {
        str(k): _safe_float(v, 0.0) for k, v in inventory.items()
    }
    live_trader: DirectClobTrader | None = None
    stale_cleanup: dict[str, Any] | None = None
    if live_mode:
        try:
            live_trader = DirectClobTrader(
                skill_root=Path(__file__).resolve().parents[1],
                client_name="polymarket-maker-rebate-bot",
            )
            prior_order_timestamps = config.get("state", {}).get("order_timestamps", {})
            if prior_order_timestamps:
                stale_cleanup = cancel_stale_orders(
                    trader=live_trader,
                    prior_order_timestamps=prior_order_timestamps,
                    stale_order_max_age_seconds=_safe_int(
                        execution.get("stale_order_max_age_seconds"),
                        DEFAULT_STALE_ORDER_MAX_AGE_SECONDS,
                    ),
                )
            raw_positions = live_trader.get_positions()
            unwind_seconds = _safe_int(
                config.get("strategy", {}).get("unwind_before_resolution_seconds"),
                DEFAULT_UNWIND_BEFORE_RESOLUTION_SECONDS,
            )
            markets = inject_held_position_markets(
                raw_positions=raw_positions,
                markets=markets,
                default_rebate_bps=params.default_rebate_bps,
                unwind_before_resolution_seconds=unwind_seconds,
            )
            inventory_notional_by_market = single_market_inventory_notional(
                raw_positions=raw_positions,
                markets=markets,
            )
        except Exception as exc:
            return {
                "status": "error",
                "error_code": "live_execution_init_failed",
                "message": str(exc),
                "dry_run": True,
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
    payload: dict[str, Any] = {
        "status": "ok",
        "skill": "polymarket-maker-rebate-bot",
        "mode": mode,
        "dry_run": mode != "live",
        "market_source": market_source,
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
    if mode == "live" and live_trader is not None:
        prior_live_risk = config.get("state", {}).get("live_risk", {})
        execution_settings = live_settings_from_execution(
            {
                **execution,
                "prior_peak_equity_usd": _safe_float(
                    prior_live_risk.get("peak_equity_usd"),
                    0.0,
                ),
            }
        )
        live_execution = execute_single_market_quotes(
            trader=live_trader,
            quotes=proposals,
            markets=markets,
            execution_settings=execution_settings,
        )
        payload["live_execution"] = live_execution
        if stale_cleanup and stale_cleanup.get("stale_count", 0) > 0:
            payload["stale_order_cleanup"] = stale_cleanup
        payload["state"] = {
            "inventory": live_execution.get("updated_inventory", {}),
            "live_risk": live_execution.get("live_risk", {}),
        }
        payload["strategy_summary"]["orders_submitted"] = len(live_execution.get("orders_submitted", []))
        payload["strategy_summary"]["open_orders"] = len(live_execution.get("open_order_ids", []))
        if isinstance(live_execution.get("live_risk"), dict):
            payload["strategy_summary"]["current_equity_usd"] = _safe_float(
                live_execution["live_risk"].get("current_equity_usd"),
                0.0,
            )
            payload["strategy_summary"]["drawdown_pct"] = _safe_float(
                live_execution["live_risk"].get("drawdown_pct"),
                0.0,
            )
        if live_execution.get("status") == "error":
            payload["status"] = "error"
            payload["error_code"] = live_execution.get("error_code")
            payload["message"] = live_execution.get("message")
    return payload


def run_unwind_all(config: dict[str, Any]) -> dict[str, Any]:
    """Emergency liquidation: cancel all orders and market-sell all positions."""
    try:
        trader = DirectClobTrader(
            skill_root=Path(__file__).resolve().parents[1],
            client_name="polymarket-maker-rebate-bot",
        )
    except Exception as exc:
        return {"status": "error", "error_code": "trader_init_failed", "message": str(exc)}

    results: dict[str, Any] = {"status": "ok", "skill": "polymarket-maker-rebate-bot", "mode": "unwind-all"}

    try:
        cancel_result = trader.cancel_all()
        results["cancel_all"] = cancel_result
    except Exception as exc:
        results["cancel_error"] = str(exc)

    try:
        raw_positions = trader.get_positions()
        sizes = positions_by_key(raw_positions)
        sell_results: list[dict[str, Any]] = []
        for token_id, shares in sizes.items():
            if shares <= 0:
                continue
            try:
                from polymarket_live import fetch_midpoint
                mid = fetch_midpoint(token_id, fallback_mid=0.5)
                sell_price = round(max(0.01, mid * 0.95), 4)
                response = trader.create_order(
                    token_id=token_id,
                    side="SELL",
                    price=sell_price,
                    size=shares,
                    tick_size="0.01",
                    neg_risk=False,
                    fee_rate_bps=0,
                )
                sell_results.append({"token_id": token_id, "shares": shares, "price": sell_price, "response": response})
            except Exception as sell_exc:
                sell_results.append({"token_id": token_id, "shares": shares, "error": str(sell_exc)})
        results["sell_results"] = sell_results
        results["positions_unwound"] = len(sell_results)
    except Exception as exc:
        results["position_error"] = str(exc)

    return results


def run_quote(config: dict[str, Any], markets_file: str | None, yes_live: bool) -> dict[str, Any]:
    try:
        markets = load_markets(config=config, markets_file=markets_file)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        return {
            "status": "error",
            "skill": "polymarket-maker-rebate-bot",
            "error_code": "quote_market_load_failed",
            "message": str(exc),
            "hint": (
                "Provide --markets-file with a saved market snapshot if "
                "live market discovery is unavailable."
            ),
            "dry_run": True,
        }
    return run_once(config=config, markets=markets, yes_live=yes_live)


def main() -> int:
    args = parse_args()
    config = load_config(args.config)

    if args.unwind_all:
        if not args.yes_live:
            result = {
                "status": "error",
                "error_code": "unwind_confirmation_required",
                "message": "Emergency unwind requires --yes-live confirmation.",
            }
        else:
            result = run_unwind_all(config=config)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("status") == "ok" else 1

    if args.run_type == "backtest":
        result = run_backtest(
            config=config,
            backtest_file=args.backtest_file,
            backtest_days_override=args.backtest_days,
        )
        if result.get("status") == "ok" and isinstance(result.get("config_updates"), dict):
            _apply_config_updates_in_place(config, result["config_updates"])
            try:
                _write_config(args.config, config)
            except Exception as exc:  # pragma: no cover - defensive runtime path
                result["config_writeback_warning"] = str(exc)
    else:
        result = run_quote(config=config, markets_file=args.markets_file, yes_live=args.yes_live)
        if isinstance(result.get("state"), dict):
            state = result["state"]
            live_exec = result.get("live_execution", {})
            if isinstance(live_exec, dict) and live_exec.get("order_timestamps"):
                state["order_timestamps"] = live_exec["order_timestamps"]
            try:
                _persist_runtime_state(args.config, config, state)
            except Exception as exc:  # pragma: no cover - defensive runtime path
                result["state_writeback_warning"] = str(exc)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
