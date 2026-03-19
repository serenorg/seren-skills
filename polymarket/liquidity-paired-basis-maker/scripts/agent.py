#!/usr/bin/env python3
"""Liquidity-filtered paired-market basis maker for Polymarket binary markets."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

# Try local bundled copy first (installed skills), then shared directory (dev repo)
_SCRIPT_DIR = Path(__file__).resolve().parent
_SHARED_DIR = Path(__file__).resolve().parents[2] / "_shared"
for _candidate in (_SCRIPT_DIR, _SHARED_DIR):
    if (_candidate / "polymarket_live.py").exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
        break

from polymarket_live import (
    DEFAULT_STALE_ORDER_MAX_AGE_SECONDS,
    DEFAULT_UNWIND_BEFORE_RESOLUTION_SECONDS,
    cancel_stale_orders,
    positions_by_key,
    DirectClobTrader,
    execute_pair_trades,
    live_settings_from_execution,
    load_live_pair_markets,
    pair_leg_exposure_notional,
    sell_held_inventory,
)
from pair_stateful_replay import (
    PairReplayParams,
    normalize_orderbook_snapshots,
    simulate_pair_backtest,
    snapshot_from_live_book,
    write_telemetry_records,
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


DISCLAIMER = (
    "This strategy can lose money. Pair relationships can break, basis can widen, "
    "and liquidity can vanish. Backtests are hypothetical and do not guarantee future "
    "performance. Use dry-run first and only trade with risk capital."
)


@dataclass(frozen=True)
class StrategyParams:
    bankroll_usd: float = 1000.0
    pairs_max: int = 8
    min_seconds_to_resolution: int = 2 * 60 * 60
    min_edge_bps: float = 2.0
    maker_rebate_bps: float = 2.3
    expected_unwind_cost_bps: float = 1.5
    adverse_selection_bps: float = 1.1
    basis_entry_bps: float = 35.0
    basis_exit_bps: float = 10.0
    expected_convergence_ratio: float = 0.35
    base_pair_notional_usd: float = 550.0
    max_notional_per_pair_usd: float = 750.0
    max_total_notional_usd: float = 1600.0
    max_leg_notional_usd: float = 800.0


@dataclass(frozen=True)
class BacktestParams:
    bankroll_usd: float = 100.0
    days: int = 90
    days_min: int = 90
    days_max: int = 365
    participation_rate: float = 0.9
    min_history_points: int = 72
    min_events: int = 120
    volatility_window_points: int = 24
    require_orderbook_history: bool = False
    spread_decay_bps: float = 45.0
    join_best_queue_factor: float = 0.85
    off_best_queue_factor: float = 0.35
    synthetic_orderbook_half_spread_bps: float = 18.0
    synthetic_orderbook_depth_usd: float = 125.0
    telemetry_path: str = ""
    min_liquidity_usd: float = 5000.0
    markets_fetch_page_size: int = 120
    max_markets: int = 80
    history_interval: str = "max"
    history_fidelity_minutes: int = 60
    gamma_markets_url: str = f"{SEREN_POLYMARKET_DATA_URL_PREFIX}/markets"
    clob_history_url: str = f"{POLYMARKET_CLOB_BASE_URL}/prices-history"
    history_fetch_workers: int = 4
    # Seren Predictions intelligence (costs SerenBucks per call)
    predictions_enabled: bool = False
    predictions_pairs_url: str = f"{SEREN_PREDICTIONS_URL_PREFIX}/api/polymarket/pairs/suggested"
    predictions_correlations_url: str = f"{SEREN_PREDICTIONS_URL_PREFIX}/api/polymarket/correlations"
    predictions_volatility_url: str = f"{SEREN_PREDICTIONS_URL_PREFIX}/api/polymarket/volatility"
    predictions_score_boost: float = 0.3  # pair score boost for prediction-confirmed pairs


@dataclass(frozen=True)
class OptimizationParams:
    enabled: bool = True
    target_return_pct: float = 25.0
    max_iterations: int = 15


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run liquidity-filtered paired-market basis maker strategy.")
    parser.add_argument("--config", default="config.json", help="Config file path.")
    parser.add_argument(
        "--run-type",
        default="backtest",
        choices=("backtest", "trade"),
        help="Run backtest only, or run trade mode after backtest gating.",
    )
    parser.add_argument("--markets-file", default=None, help="Optional trade market JSON file.")
    parser.add_argument(
        "--backtest-file",
        default=None,
        help="Optional paired backtest fixture JSON file with history and orderbook snapshots.",
    )
    parser.add_argument("--backtest-days", type=int, default=None, help="Override backtest days.")
    parser.add_argument(
        "--allow-negative-backtest",
        action="store_true",
        help="Allow trade mode even if backtest return is <= 0.",
    )
    parser.add_argument("--yes-live", action="store_true", help="Explicit live execution confirmation.")
    parser.add_argument("--unwind-all", action="store_true", help="Emergency liquidation: cancel all orders and market-sell all positions.")
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


def _canonicalize_history_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path.endswith("/trades"):
        path = parsed.path[: -len("/trades")] + "/prices-history"
        return urlunparse(parsed._replace(path=path))
    return url


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def load_json(path: Path) -> dict[str, Any] | list[Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(config_path: str) -> dict[str, Any]:
    payload = load_json(Path(config_path))
    return payload if isinstance(payload, dict) else {}


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


def to_strategy_params(config: dict[str, Any]) -> StrategyParams:
    raw = config.get("strategy", {})
    return StrategyParams(
        bankroll_usd=max(1.0, _safe_float(raw.get("bankroll_usd"), 1000.0)),
        pairs_max=max(1, _safe_int(raw.get("pairs_max"), 8)),
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
        base_pair_notional_usd=max(1.0, _safe_float(raw.get("base_pair_notional_usd"), 550.0)),
        max_notional_per_pair_usd=max(1.0, _safe_float(raw.get("max_notional_per_pair_usd"), 750.0)),
        max_total_notional_usd=max(1.0, _safe_float(raw.get("max_total_notional_usd"), 1600.0)),
        max_leg_notional_usd=max(1.0, _safe_float(raw.get("max_leg_notional_usd"), 800.0)),
    )


def to_backtest_params(config: dict[str, Any]) -> BacktestParams:
    raw = config.get("backtest", {})
    range_raw = raw.get("days_range", {}) if isinstance(raw.get("days_range"), dict) else {}
    days_min = max(7, _safe_int(range_raw.get("min"), 90))
    days_max = max(days_min, _safe_int(range_raw.get("max"), 365))
    days = int(clamp(_safe_int(raw.get("days"), 90), days_min, days_max))
    return BacktestParams(
        bankroll_usd=max(1.0, _safe_float(raw.get("bankroll_usd"), 100.0)),
        days=days,
        days_min=days_min,
        days_max=days_max,
        participation_rate=clamp(_safe_float(raw.get("participation_rate"), 0.9), 0.0, 1.0),
        min_history_points=max(8, _safe_int(raw.get("min_history_points"), 72)),
        min_events=max(1, _safe_int(raw.get("min_events"), 120)),
        volatility_window_points=max(3, _safe_int(raw.get("volatility_window_points"), 24)),
        require_orderbook_history=_safe_bool(raw.get("require_orderbook_history"), False),
        spread_decay_bps=max(1.0, _safe_float(raw.get("spread_decay_bps"), 45.0)),
        join_best_queue_factor=clamp(_safe_float(raw.get("join_best_queue_factor"), 0.85), 0.0, 1.0),
        off_best_queue_factor=clamp(_safe_float(raw.get("off_best_queue_factor"), 0.35), 0.0, 1.0),
        synthetic_orderbook_half_spread_bps=max(
            0.1,
            _safe_float(raw.get("synthetic_orderbook_half_spread_bps"), 18.0),
        ),
        synthetic_orderbook_depth_usd=max(
            0.0,
            _safe_float(raw.get("synthetic_orderbook_depth_usd"), 125.0),
        ),
        telemetry_path=_safe_str(raw.get("telemetry_path"), ""),
        min_liquidity_usd=max(0.0, _safe_float(raw.get("min_liquidity_usd"), 5000.0)),
        markets_fetch_page_size=max(25, _safe_int(raw.get("markets_fetch_page_size"), 120)),
        max_markets=max(0, _safe_int(raw.get("max_markets"), 80)),
        history_interval=_safe_str(raw.get("history_interval"), "max"),
        history_fidelity_minutes=max(1, _safe_int(raw.get("history_fidelity_minutes"), 60)),
        gamma_markets_url=_safe_str(raw.get("gamma_markets_url"), f"{SEREN_POLYMARKET_DATA_URL_PREFIX}/markets"),
        clob_history_url=_canonicalize_history_url(
            _safe_str(raw.get("clob_history_url"), f"{POLYMARKET_CLOB_BASE_URL}/prices-history")
        ),
        history_fetch_workers=max(1, _safe_int(raw.get("history_fetch_workers"), 4)),
        predictions_enabled=bool(raw.get("predictions_enabled", False)),
        predictions_score_boost=_safe_float(raw.get("predictions_score_boost"), 0.3),
    )


def to_optimization_params(config: dict[str, Any]) -> OptimizationParams:
    backtest_raw = config.get("backtest", {})
    raw = backtest_raw.get("optimization", {}) if isinstance(backtest_raw, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    return OptimizationParams(
        enabled=_safe_bool(raw.get("enabled"), True),
        target_return_pct=_safe_float(raw.get("target_return_pct"), 25.0),
        max_iterations=max(1, _safe_int(raw.get("max_iterations"), 15)),
    )


def _to_pair_replay_params(p: StrategyParams, bt: BacktestParams) -> PairReplayParams:
    return PairReplayParams(
        bankroll_usd=bt.bankroll_usd,
        min_seconds_to_resolution=p.min_seconds_to_resolution,
        min_edge_bps=p.min_edge_bps,
        maker_rebate_bps=p.maker_rebate_bps,
        expected_unwind_cost_bps=p.expected_unwind_cost_bps,
        adverse_selection_bps=p.adverse_selection_bps,
        basis_entry_bps=p.basis_entry_bps,
        basis_exit_bps=p.basis_exit_bps,
        expected_convergence_ratio=p.expected_convergence_ratio,
        base_pair_notional_usd=p.base_pair_notional_usd,
        max_notional_per_pair_usd=p.max_notional_per_pair_usd,
        max_total_notional_usd=p.max_total_notional_usd,
        max_leg_notional_usd=p.max_leg_notional_usd,
        participation_rate=bt.participation_rate,
        min_history_points=bt.min_history_points,
        volatility_window_points=bt.volatility_window_points,
        require_orderbook_history=bt.require_orderbook_history,
        spread_decay_bps=bt.spread_decay_bps,
        join_best_queue_factor=bt.join_best_queue_factor,
        off_best_queue_factor=bt.off_best_queue_factor,
        synthetic_orderbook_half_spread_bps=bt.synthetic_orderbook_half_spread_bps,
        synthetic_orderbook_depth_usd=bt.synthetic_orderbook_depth_usd,
        telemetry_path=bt.telemetry_path,
    )


def _normalize_history(
    raw_history: Any,
    start_ts: int,
    end_ts: int,
    *,
    token_id: str = "",
) -> list[tuple[int, float]]:
    points: list[tuple[int, float]] = []
    fallback_points: list[tuple[int, float]] = []
    seen: set[int] = set()
    fallback_seen: set[int] = set()

    rows = _extract_history_rows(raw_history)
    if not rows:
        return points

    for item in rows:
        parsed = _history_point_from_row(item, token_id=token_id)
        if parsed is None:
            continue
        t, p = parsed
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


def _coerce_unix_ts(value: Any) -> int:
    if isinstance(value, int | float):
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
    if not observed:
        return True
    return token in observed


def _history_point_from_row(row: Any, token_id: str) -> tuple[int, float] | None:
    if isinstance(row, list | tuple) and len(row) >= 2:
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


def _parse_iso_ts(value: Any) -> int | None:
    raw = _safe_str(value, "")
    if not raw:
        return None
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


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
    req = Request(
        url,
        headers={
            "User-Agent": "liquidity-paired-basis-maker/1.1",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
        return _unwrap_seren_response(raw)


def _http_get_json_public(url: str, timeout: int = 30) -> dict[str, Any] | list[Any]:
    req = Request(
        url,
        headers={
            "User-Agent": "liquidity-paired-basis-maker/1.1",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
        return _unwrap_seren_response(raw)


def _http_get_json(url: str, timeout: int = 30) -> dict[str, Any] | list[Any]:
    if _is_clob_direct_url(url):
        return _http_get_json_public(url, timeout=timeout)

    _seren_publisher_target(url)

    api_key = _runtime_api_key()
    if not api_key:
        raise RuntimeError(MISSING_RUNTIME_AUTH_ERROR)
    return _http_get_json_via_api_key(url, api_key=api_key, timeout=timeout)


def _align_histories(primary: list[tuple[int, float]], secondary: list[tuple[int, float]]) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    index_secondary = {t: p for t, p in secondary}
    aligned_primary: list[tuple[int, float]] = []
    aligned_secondary: list[tuple[int, float]] = []
    for t, p1 in primary:
        p2 = index_secondary.get(t)
        if p2 is None:
            continue
        aligned_primary.append((t, p1))
        aligned_secondary.append((t, p2))
    return aligned_primary, aligned_secondary


def _combine_orderbook_mode(primary_mode: str, pair_mode: str) -> str:
    if primary_mode == pair_mode:
        return primary_mode
    return f"{primary_mode}|{pair_mode}"


def _fetch_live_backtest_pairs(p: StrategyParams, bt: BacktestParams, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
    replay_params = _to_pair_replay_params(p, bt)
    offset = 0
    candidates: list[dict[str, Any]] = []
    seen_token_ids: set[str] = set()

    pages = 0
    while True:
        pages += 1
        if pages > 200:
            break
        query = urlencode(
            {
                "active": "true",
                "closed": "false",
                "limit": bt.markets_fetch_page_size,
                "offset": offset,
                "order": "volume24hr",
                "ascending": "false",
            }
        )
        raw = _http_get_json(f"{bt.gamma_markets_url}?{query}")
        if not isinstance(raw, list) or not raw:
            break

        added_on_page = 0
        for market in raw:
            if not isinstance(market, dict):
                continue
            liquidity = _safe_float(market.get("liquidity"), 0.0)
            if liquidity < bt.min_liquidity_usd:
                continue

            end_market = _parse_iso_ts(market.get("endDate")) or _safe_int(market.get("end_ts"), end_ts + 86400)
            if end_market <= start_ts + p.min_seconds_to_resolution:
                continue

            token_ids = _json_to_list(market.get("clobTokenIds"))
            if not token_ids:
                continue
            token_id = _safe_str(token_ids[0], "")
            if not token_id or token_id in seen_token_ids:
                continue
            seen_token_ids.add(token_id)

            events = _json_to_list(market.get("events"))
            event_id = ""
            if events and isinstance(events[0], dict):
                event_id = _safe_str(events[0].get("id"), "")
            if not event_id:
                event_id = _safe_str(market.get("seriesSlug"), "")
            if not event_id:
                event_id = _safe_str(market.get("category"), "misc")

            market_id = _safe_str(market.get("id"), token_id)
            # Score by market-making quality for ranking
            outcome_prices = _json_to_list(market.get("outcomePrices"))
            mid_price = _safe_float(outcome_prices[0] if outcome_prices else None, 0.5)
            spread = _safe_float(market.get("spread"), 1.0)
            volume24hr = _safe_float(market.get("volume24hr"), 0.0)
            price_score = 1.0 - abs(mid_price - 0.5) * 2.0
            spread_score = max(0.0, 1.0 - spread * 10.0)
            volume_score = min(1.0, volume24hr / 50000.0)
            mm_score = price_score * 0.4 + volume_score * 0.4 + spread_score * 0.2
            candidates.append(
                {
                    "market_id": market_id,
                    "question": _safe_str(market.get("question"), market_id),
                    "token_id": token_id,
                    "event_id": event_id,
                    "end_ts": end_market,
                    "rebate_bps": _safe_float(market.get("rebate_bps"), p.maker_rebate_bps),
                    "volume24hr": volume24hr,
                    "mm_score": mm_score,
                }
            )
            added_on_page += 1

        if added_on_page == 0:
            break
        offset += len(raw)
        if len(raw) < bt.markets_fetch_page_size:
            break

    # Sort candidates by mm_score before fetching history for all of them
    candidates.sort(key=lambda c: c.get("mm_score", 0.0), reverse=True)

    def _fetch_candidate_history(candidate: dict[str, Any]) -> dict[str, Any] | None:
        history_limit = max(bt.min_history_points * 12, 1000)
        queries = (
            {"market": candidate["token_id"], "interval": bt.history_interval, "fidelity": bt.history_fidelity_minutes},
            {"market": candidate["token_id"], "limit": history_limit},
        )
        payload = None
        for params in queries:
            try:
                payload = _http_get_json(f"{bt.clob_history_url}?{urlencode(params)}")
                if payload:
                    break
            except Exception:
                continue
        if payload is None:
            return None
        history = _normalize_history(
            payload,
            start_ts=start_ts,
            end_ts=end_ts,
            token_id=candidate["token_id"],
        )
        if len(history) < bt.min_history_points:
            return None
        try:
            book_payload = _http_get_json_public(
                f"{POLYMARKET_CLOB_BASE_URL}/book?{urlencode({'token_id': candidate['token_id']})}"
            )
        except Exception:
            book_payload = None
        orderbooks, orderbook_mode = snapshot_from_live_book(
            payload=book_payload,
            history=history,
            params=replay_params,
        )
        return {
            **candidate,
            "history": history,
            "orderbooks": orderbooks,
            "orderbook_mode": orderbook_mode,
        }

    with_history: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, bt.history_fetch_workers)) as executor:
        futures = [executor.submit(_fetch_candidate_history, candidate) for candidate in candidates]
        for future in as_completed(futures):
            row = future.result()
            if row:
                with_history.append(row)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in with_history:
        grouped[_safe_str(row.get("event_id"), "misc")].append(row)

    pairs: list[dict[str, Any]] = []
    for event_id, group in grouped.items():
        if len(group) < 2:
            continue
        group_sorted = sorted(group, key=lambda row: _safe_float(row.get("volume24hr"), 0.0), reverse=True)
        for i in range(len(group_sorted) - 1):
            primary = group_sorted[i]
            secondary = group_sorted[i + 1]
            h1, h2 = _align_histories(primary["history"], secondary["history"])
            if len(h1) < bt.min_history_points:
                continue
            pair_id = _safe_str(secondary.get("market_id"), "unknown")
            market_id = _safe_str(primary.get("market_id"), "unknown")
            pairs.append(
                {
                    "market_id": market_id,
                    "pair_market_id": pair_id,
                    "question": _safe_str(primary.get("question"), market_id),
                    "pair_question": _safe_str(secondary.get("question"), pair_id),
                    "token_id": _safe_str(primary.get("token_id"), ""),
                    "pair_token_id": _safe_str(secondary.get("token_id"), ""),
                    "event_id": event_id,
                    "end_ts": min(_safe_int(primary.get("end_ts"), end_ts + 86400), _safe_int(secondary.get("end_ts"), end_ts + 86400)),
                    "rebate_bps": (_safe_float(primary.get("rebate_bps"), p.maker_rebate_bps) + _safe_float(secondary.get("rebate_bps"), p.maker_rebate_bps)) / 2.0,
                    "history": h1,
                    "pair_history": h2,
                    "orderbooks": primary.get("orderbooks", {}),
                    "pair_orderbooks": secondary.get("orderbooks", {}),
                    "orderbook_mode": _combine_orderbook_mode(
                        _safe_str(primary.get("orderbook_mode"), "unknown"),
                        _safe_str(secondary.get("orderbook_mode"), "unknown"),
                    ),
                    "source": "live-seren-publisher",
                }
            )

    if pairs:
        return pairs

    # Fallback when event-level metadata is sparse: pair adjacent markets by liquidity.
    fallback_sorted = sorted(with_history, key=lambda row: _safe_float(row.get("volume24hr"), 0.0), reverse=True)
    for i in range(0, len(fallback_sorted) - 1, 2):
        primary = fallback_sorted[i]
        secondary = fallback_sorted[i + 1]
        h1, h2 = _align_histories(primary["history"], secondary["history"])
        if len(h1) < bt.min_history_points:
            continue
        pair_id = _safe_str(secondary.get("market_id"), "unknown")
        market_id = _safe_str(primary.get("market_id"), "unknown")
        pairs.append(
            {
                "market_id": market_id,
                "pair_market_id": pair_id,
                "question": _safe_str(primary.get("question"), market_id),
                "pair_question": _safe_str(secondary.get("question"), pair_id),
                "token_id": _safe_str(primary.get("token_id"), ""),
                "pair_token_id": _safe_str(secondary.get("token_id"), ""),
                "event_id": "fallback",
                "end_ts": min(_safe_int(primary.get("end_ts"), end_ts + 86400), _safe_int(secondary.get("end_ts"), end_ts + 86400)),
                "rebate_bps": (_safe_float(primary.get("rebate_bps"), p.maker_rebate_bps) + _safe_float(secondary.get("rebate_bps"), p.maker_rebate_bps)) / 2.0,
                "history": h1,
                "pair_history": h2,
                "orderbooks": primary.get("orderbooks", {}),
                "pair_orderbooks": secondary.get("orderbooks", {}),
                "orderbook_mode": _combine_orderbook_mode(
                    _safe_str(primary.get("orderbook_mode"), "unknown"),
                    _safe_str(secondary.get("orderbook_mode"), "unknown"),
                ),
                "source": "live-seren-publisher-fallback",
            }
        )

    return pairs


def _load_markets_from_fixture(
    *,
    fixture_path: Path,
    p: StrategyParams,
    bt: BacktestParams,
    start_ts: int,
    end_ts: int,
) -> list[dict[str, Any]]:
    replay_params = _to_pair_replay_params(p, bt)
    payload = load_json(fixture_path)
    if isinstance(payload, dict):
        rows = payload.get("markets", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    if not isinstance(rows, list):
        return []

    markets: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        history = _normalize_history(
            row.get("history"),
            start_ts=start_ts,
            end_ts=end_ts,
            token_id=_safe_str(row.get("token_id"), _safe_str(row.get("market_id"), f"fixture-{idx}-a")),
        )
        pair_history = _normalize_history(
            row.get("pair_history"),
            start_ts=start_ts,
            end_ts=end_ts,
            token_id=_safe_str(row.get("pair_token_id"), _safe_str(row.get("pair_market_id"), f"fixture-{idx}-b")),
        )
        if len(history) < bt.min_history_points or len(pair_history) < bt.min_history_points:
            continue
        orderbooks, primary_mode = normalize_orderbook_snapshots(
            row.get("orderbooks"),
            history,
            replay_params,
        )
        pair_orderbooks, pair_mode = normalize_orderbook_snapshots(
            row.get("pair_orderbooks"),
            pair_history,
            replay_params,
        )
        markets.append(
            {
                "market_id": _safe_str(row.get("market_id"), f"fixture-{idx}-a"),
                "pair_market_id": _safe_str(row.get("pair_market_id"), f"fixture-{idx}-b"),
                "question": _safe_str(row.get("question"), _safe_str(row.get("market_id"), f"fixture-{idx}-a")),
                "pair_question": _safe_str(
                    row.get("pair_question"),
                    _safe_str(row.get("pair_market_id"), f"fixture-{idx}-b"),
                ),
                "token_id": _safe_str(row.get("token_id"), _safe_str(row.get("market_id"), f"fixture-{idx}-a")),
                "pair_token_id": _safe_str(
                    row.get("pair_token_id"),
                    _safe_str(row.get("pair_market_id"), f"fixture-{idx}-b"),
                ),
                "event_id": _safe_str(row.get("event_id"), f"fixture-{idx}"),
                "end_ts": _safe_int(row.get("end_ts"), end_ts + 86400),
                "rebate_bps": _safe_float(row.get("rebate_bps"), p.maker_rebate_bps),
                "history": history,
                "pair_history": pair_history,
                "orderbooks": orderbooks,
                "pair_orderbooks": pair_orderbooks,
                "orderbook_mode": _combine_orderbook_mode(primary_mode, pair_mode),
                "source": "fixture",
            }
        )
    return markets


def _load_backtest_markets(
    p: StrategyParams,
    bt: BacktestParams,
    start_ts: int,
    end_ts: int,
    backtest_file: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if backtest_file:
        fixture_path = Path(backtest_file)
        if not fixture_path.exists():
            raise FileNotFoundError(f"Backtest fixture not found: {fixture_path}")
        return (
            _load_markets_from_fixture(
                fixture_path=fixture_path,
                p=p,
                bt=bt,
                start_ts=start_ts,
                end_ts=end_ts,
            ),
            f"fixture:{fixture_path.name}",
        )
    return _fetch_live_backtest_pairs(p=p, bt=bt, start_ts=start_ts, end_ts=end_ts), "live-seren-publisher"


def _max_drawdown_stats(equity_curve: list[float]) -> tuple[float, float]:
    if not equity_curve:
        return 0.0, 0.0
    bankroll = equity_curve[0]
    peak = float("-inf")
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        drawdown = peak - value
        drawdown_pct = (drawdown / peak) * 100.0 if peak > 0 else 0.0
        max_dd_usd = max(max_dd_usd, drawdown)
        max_dd_pct = max(max_dd_pct, drawdown_pct)
    return min(max_dd_usd, bankroll), min(max_dd_pct, 100.0)


def _annualized_return_pct(starting: float, ending: float, days: int) -> float:
    if starting <= 0 or ending <= 0 or days <= 0:
        return 0.0
    return ((ending / starting) ** (365.0 / float(days)) - 1.0) * 100.0


def _sharpe_like_score(event_pnls: list[float], bankroll_usd: float, days: int) -> float:
    if bankroll_usd <= 0 or days <= 0 or len(event_pnls) < 2:
        return 0.0
    per_event_returns = [pnl / bankroll_usd for pnl in event_pnls]
    mean = sum(per_event_returns) / len(per_event_returns)
    stdev = pstdev(per_event_returns)
    if stdev <= 1e-12:
        return 0.0
    events_per_year = max(1.0, len(per_event_returns) * (365.0 / float(days)))
    return (mean / stdev) * math.sqrt(events_per_year)


def _simulate_pair(market: dict[str, Any], p: StrategyParams, bt: BacktestParams, allocated_capital: float = 0.0) -> dict[str, Any]:
    replay_params = _to_pair_replay_params(p, bt)
    history = market.get("history", [])
    pair_history = market.get("pair_history", [])
    orderbooks = market.get("orderbooks")
    pair_orderbooks = market.get("pair_orderbooks")
    orderbook_mode = _safe_str(market.get("orderbook_mode"), "")
    if not isinstance(orderbooks, dict):
        orderbooks, primary_mode = normalize_orderbook_snapshots([], history, replay_params)
        market = {**market, "orderbooks": orderbooks}
        orderbook_mode = primary_mode
    else:
        primary_mode = orderbook_mode.split("|", 1)[0] if orderbook_mode else "unknown"
    if not isinstance(pair_orderbooks, dict):
        pair_orderbooks, pair_mode = normalize_orderbook_snapshots([], pair_history, replay_params)
        market = {**market, "pair_orderbooks": pair_orderbooks}
        orderbook_mode = _combine_orderbook_mode(primary_mode, pair_mode)
    if orderbook_mode:
        market = {**market, "orderbook_mode": orderbook_mode}
    result = simulate_pair_backtest(market=market, params=replay_params, allocated_capital=allocated_capital)
    return {
        **result,
        "traded_points": result["quoted_points"],
    }


def _pair_target_descriptors(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "market_id": _safe_str(market.get("market_id"), "unknown"),
            "pair_market_id": _safe_str(market.get("pair_market_id"), "unknown"),
            "question": _safe_str(market.get("question"), ""),
            "pair_question": _safe_str(market.get("pair_question"), ""),
        }
        for market in markets
    ]


def _diff_section(original: dict[str, Any], updated: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    for key, value in updated.items():
        if original.get(key) != value:
            diff[key] = value
    return diff


def _rank_pair_markets(markets: list[dict[str, Any]], result: dict[str, Any]) -> list[dict[str, Any]]:
    pnl_by_pair: dict[tuple[str, str], float] = {}
    for row in result.get("pairs", []):
        if not isinstance(row, dict):
            continue
        key = (
            _safe_str(row.get("market_id"), "unknown"),
            _safe_str(row.get("pair_market_id"), "unknown"),
        )
        pnl_by_pair[key] = _safe_float(row.get("pnl_usd"), 0.0)
    return sorted(
        markets,
        key=lambda market: (
            pnl_by_pair.get(
                (
                    _safe_str(market.get("market_id"), "unknown"),
                    _safe_str(market.get("pair_market_id"), "unknown"),
                ),
                float("-inf"),
            ),
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


def _pair_optimization_candidates(config: dict[str, Any], total_markets: int) -> list[dict[str, Any]]:
    p = to_strategy_params(config)
    base_pairs = max(2, min(total_markets, p.pairs_max))
    focus_pairs = max(2, min(total_markets, max(2, int(round(base_pairs * 0.6)))))
    broad_pairs = max(2, min(total_markets, max(base_pairs, int(round(total_markets * 0.75)))))
    return [
        {
            "name": "focus-lower-entry",
            "subset_size": focus_pairs,
            "strategy": {
                "pairs_max": focus_pairs,
                "basis_entry_bps": round(max(10.0, p.basis_entry_bps * 0.8), 4),
                "expected_convergence_ratio": round(clamp(p.expected_convergence_ratio + 0.1, 0.0, 1.0), 4),
            },
            "backtest": {},
        },
        {
            "name": "focus-higher-capacity",
            "subset_size": focus_pairs,
            "strategy": {
                "pairs_max": focus_pairs,
                "basis_entry_bps": round(max(10.0, p.basis_entry_bps * 0.75), 4),
                "min_edge_bps": round(max(0.5, p.min_edge_bps * 0.75), 4),
                "expected_convergence_ratio": round(clamp(p.expected_convergence_ratio + 0.15, 0.0, 1.0), 4),
                "base_pair_notional_usd": round(p.base_pair_notional_usd * 1.15, 4),
                "max_notional_per_pair_usd": round(p.max_notional_per_pair_usd * 1.15, 4),
                "max_total_notional_usd": round(p.max_total_notional_usd * 1.15, 4),
                "max_leg_notional_usd": round(p.max_leg_notional_usd * 1.1, 4),
            },
            "backtest": {},
        },
        {
            "name": "balanced-tighter-exit",
            "subset_size": base_pairs,
            "strategy": {
                "pairs_max": base_pairs,
                "basis_entry_bps": round(max(10.0, p.basis_entry_bps * 0.85), 4),
                "basis_exit_bps": round(max(0.0, p.basis_exit_bps * 0.8), 4),
                "expected_convergence_ratio": round(clamp(p.expected_convergence_ratio + 0.05, 0.0, 1.0), 4),
            },
            "backtest": {},
        },
        {
            "name": "broad-higher-capacity",
            "subset_size": broad_pairs,
            "strategy": {
                "pairs_max": broad_pairs,
                "basis_entry_bps": round(max(10.0, p.basis_entry_bps * 0.9), 4),
                "base_pair_notional_usd": round(p.base_pair_notional_usd * 1.1, 4),
                "max_total_notional_usd": round(p.max_total_notional_usd * 1.2, 4),
                "max_leg_notional_usd": round(p.max_leg_notional_usd * 1.1, 4),
            },
            "backtest": {},
        },
    ]


def _check_serenbucks_balance(api_key: str) -> float:
    """Check SerenBucks balance. Returns balance in USD or 0.0 on error."""
    try:
        request = Request(
            "https://api.serendb.com/wallet/balance",
            headers={
                "User-Agent": "liquidity-paired-basis-maker/1.1",
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


def _fetch_predictions_pair_signals(
    backtest_params: "BacktestParams",
) -> dict[str, Any]:
    """Fetch pair-specific intelligence from Seren Predictions computed endpoints.

    Uses GET /api/polymarket/pairs/suggested and GET /api/polymarket/correlations
    to enrich pair selection with cross-platform basis deviation and correlation data.
    Returns dict with 'suggested_pairs' and 'correlations' keys. Costs SerenBucks.
    """
    if not backtest_params.predictions_enabled:
        return {}

    api_key = os.getenv("API_KEY", "").strip() or os.getenv("SEREN_API_KEY", "").strip()
    if not api_key:
        return {}

    balance = _check_serenbucks_balance(api_key)
    estimated_cost = 0.20  # pairs/suggested ($0.10) + correlations ($0.10)
    if balance < estimated_cost:
        print(
            f"WARNING: SerenBucks balance (${balance:.2f}) may be insufficient for "
            f"predictions intelligence (estimated ${estimated_cost:.2f}). "
            f"Top up at https://serendb.com/billing to use Seren Predictions.",
            file=sys.stderr,
        )
        if balance <= 0.0:
            return {}

    result: dict[str, Any] = {}

    # Fetch suggested pairs ranked by basis deviation sigma
    try:
        params = urlencode({"min_r": "0.70", "min_basis_deviation_sigma": "1.0", "days": "90", "limit": "50"})
        url = f"{backtest_params.predictions_pairs_url}?{params}"
        request = Request(
            url,
            headers={
                "User-Agent": "liquidity-paired-basis-maker/1.1",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, dict):
                result["suggested_pairs"] = data.get("pairs", data.get("results", []))
            elif isinstance(data, list):
                result["suggested_pairs"] = data
    except Exception:
        pass

    # Fetch correlations for pair validation
    try:
        params = urlencode({"min_r": "0.70", "days": "90", "limit": "100"})
        url = f"{backtest_params.predictions_correlations_url}?{params}"
        request = Request(
            url,
            headers={
                "User-Agent": "liquidity-paired-basis-maker/1.1",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, dict):
                result["correlations"] = data.get("pairs", data.get("results", []))
            elif isinstance(data, list):
                result["correlations"] = data
    except Exception:
        pass

    return result


def _evaluate_backtest(
    *,
    config: dict[str, Any],
    markets: list[dict[str, Any]],
    source: str,
    days: int,
    start_ts: int,
    end_ts: int,
    skill_name: str,
) -> dict[str, Any]:
    p = to_strategy_params(config)
    bt = to_backtest_params(config)
    p = replace(p, bankroll_usd=bt.bankroll_usd)
    summaries: list[dict[str, Any]] = []
    event_pnls: list[float] = []
    considered = 0
    quoted = 0
    skipped = 0
    fill_events = 0
    total_notional = 0.0
    telemetry: list[dict[str, Any]] = []
    orderbook_modes: dict[str, int] = defaultdict(int)
    capital_per_pair = p.bankroll_usd / max(1, len(markets))

    for market in markets:
        result = _simulate_pair(market, p, bt, allocated_capital=capital_per_pair)
        summaries.append(
            {
                "market_id": result["market_id"],
                "pair_market_id": result["pair_market_id"],
                "considered_points": result["considered_points"],
                "quoted_points": result["quoted_points"],
                "traded_points": result["traded_points"],
                "skipped_points": result["skipped_points"],
                "fill_events": result["fill_events"],
                "filled_notional_usd": result["filled_notional_usd"],
                "pnl_usd": result["pnl_usd"],
                "orderbook_mode": result["orderbook_mode"],
            }
        )
        considered += int(result["considered_points"])
        quoted += int(result["quoted_points"])
        skipped += int(result["skipped_points"])
        fill_events += int(result["fill_events"])
        total_notional += float(result["filled_notional_usd"])
        event_pnls.extend(result["event_pnls"])
        telemetry.extend(result.get("telemetry", []))
        orderbook_modes[_safe_str(result.get("orderbook_mode"), "unknown")] += 1

    equity_curve = [p.bankroll_usd]
    equity = p.bankroll_usd
    for event_pnl in event_pnls:
        equity = max(0.0, equity + event_pnl)
        equity_curve.append(equity)

    total_pnl = equity - p.bankroll_usd
    total_return_pct = (total_pnl / p.bankroll_usd) * 100.0
    max_drawdown_usd, max_drawdown_pct = _max_drawdown_stats(equity_curve)
    display_total_return_pct = max(total_return_pct, -100.0)
    display_max_drawdown_pct = min(max_drawdown_pct, 100.0)
    events = len(event_pnls)
    hit_rate_pct = ((sum(1 for pnl in event_pnls if pnl > 0.0) / events) * 100.0) if events else 0.0
    annualized_return_pct = _annualized_return_pct(starting=p.bankroll_usd, ending=equity, days=days)
    sharpe_like = _sharpe_like_score(event_pnls=event_pnls, bankroll_usd=p.bankroll_usd, days=days)
    turnover_multiple = (total_notional / p.bankroll_usd) if p.bankroll_usd > 0 else 0.0

    if events < bt.min_events:
        return {
            "status": "error",
            "error_code": "insufficient_sample_size",
            "message": (
                "Backtest blocked because event sample is too small for decision-grade metrics. "
                f"Required at least {bt.min_events}, observed {events}."
            ),
            "dry_run": True,
            "backtest_summary": {
                "days": days,
                "source": source,
                "pairs_loaded": len(markets),
                "events_observed": events,
                "quoted_points": quoted,
                "fill_events": fill_events,
                "min_events_required": bt.min_events,
                "orderbook_modes": dict(sorted(orderbook_modes.items())),
            },
            "disclaimer": DISCLAIMER,
        }

    write_telemetry_records(bt.telemetry_path, telemetry)
    predictions = _fetch_predictions_pair_signals(backtest_params=bt)
    predictions_pairs_count = len(predictions.get("suggested_pairs", []))
    predictions_correlations_count = len(predictions.get("correlations", []))

    return {
        "status": "ok",
        "skill": skill_name,
        "mode": "backtest",
        "dry_run": True,
        "predictions_intelligence": {
            "enabled": bt.predictions_enabled,
            "publisher": SEREN_PREDICTIONS_PUBLISHER,
            "suggested_pairs_found": predictions_pairs_count,
            "correlations_found": predictions_correlations_count,
            "endpoints_used": [
                "GET /api/polymarket/pairs/suggested ($0.10)",
                "GET /api/polymarket/correlations ($0.10)",
            ],
            "note": (
                "Seren Predictions intelligence active — costs SerenBucks per run."
                if bt.predictions_enabled
                else "Disabled. Set predictions_enabled: true in config to activate (costs SerenBucks)."
            ),
        },
        "backtest_summary": {
            "days": days,
            "days_range": {"min": bt.days_min, "max": bt.days_max},
            "start_utc": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
            "end_utc": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
            "source": source,
            "pairs_selected": len(summaries),
            "considered_points": considered,
            "quoted_points": quoted,
            "traded_points": quoted,
            "skipped_points": skipped,
            "fill_events": fill_events,
            "trade_rate_pct": round((quoted / considered) * 100.0 if considered else 0.0, 4),
            "orderbook_modes": dict(sorted(orderbook_modes.items())),
            "telemetry_path": bt.telemetry_path,
            "telemetry_records": len(telemetry),
        },
        "results": {
            "starting_bankroll_usd": round(p.bankroll_usd, 2),
            "ending_bankroll_usd": round(equity, 2),
            "total_pnl_usd": round(total_pnl, 4),
            "return_pct": round(display_total_return_pct, 4),
            "total_return_pct": round(display_total_return_pct, 4),
            "annualized_return_pct": round(annualized_return_pct, 4),
            "sharpe_like_score": round(sharpe_like, 4),
            "hit_rate_pct": round(hit_rate_pct, 4),
            "filled_notional_usd": round(total_notional, 2),
            "turnover_multiple": round(turnover_multiple, 4),
            "events": events,
            "fill_events": fill_events,
            "min_events_required": bt.min_events,
            "max_drawdown_usd": round(max_drawdown_usd, 4),
            "max_drawdown_pct": round(display_max_drawdown_pct, 4),
            "decision_hint": "consider_trade_mode" if total_pnl > 0 else "paper_only_or_tune",
        },
        "pairs": sorted(summaries, key=lambda row: row["pnl_usd"], reverse=True),
        "disclaimer": DISCLAIMER,
        **(
            {
                "predictions_upgrade_prompt": {
                    "message": (
                        "Improve your pair selection with Seren Predictions intelligence. "
                        "Cross-platform correlation analysis and basis deviation signals can "
                        "identify stronger pairs and better entry timing. "
                        "Estimated cost: ~$0.20 per backtest run."
                    ),
                    "action": 'Set "predictions_enabled": true in your config.json backtest section.',
                    "publisher": "seren-polymarket-intelligence",
                    "estimated_cost_usd": 0.20,
                    "endpoints_used": [
                        "GET /api/polymarket/pairs/suggested ($0.10)",
                        "GET /api/polymarket/correlations ($0.10)",
                    ],
                    "benefits": [
                        "Validate pair correlation strength with cross-platform data",
                        "Rank pairs by basis deviation sigma for better entry signals",
                        "Filter for pairs where cross-platform data confirms mean-reversion potential",
                    ],
                },
            }
            if not bt.predictions_enabled
            else {}
        ),
    }


def _optimize_backtest(
    *,
    config: dict[str, Any],
    markets: list[dict[str, Any]],
    source: str,
    days: int,
    start_ts: int,
    end_ts: int,
    skill_name: str,
) -> dict[str, Any]:
    baseline = _evaluate_backtest(
        config=config,
        markets=markets,
        source=source,
        days=days,
        start_ts=start_ts,
        end_ts=end_ts,
        skill_name=skill_name,
    )
    if baseline.get("status") != "ok":
        return baseline

    optimization = to_optimization_params(config)
    ranked_markets = _rank_pair_markets(markets, baseline)
    best_result = baseline
    best_config = _clone_config(config)
    best_targets = _pair_target_descriptors(ranked_markets)
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
        for candidate in _pair_optimization_candidates(config, len(ranked_markets))[: max(0, max_attempts - 1)]:
            if _safe_float(best_result.get("results", {}).get("return_pct"), 0.0) >= optimization.target_return_pct:
                break
            subset_size = max(2, min(len(ranked_markets), _safe_int(candidate.get("subset_size"), len(ranked_markets))))
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
                skill_name=skill_name,
            )
            candidate_targets = _pair_target_descriptors(candidate_markets)
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
        "target_pairs": best_targets,
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
    backtest_days: int | None,
    backtest_file: str | None = None,
) -> dict[str, Any]:
    p = to_strategy_params(config)
    bt = to_backtest_params(config)
    days = int(clamp(backtest_days if backtest_days is not None else bt.days, bt.days_min, bt.days_max))
    configured_backtest_file = ""
    if isinstance(config.get("backtest"), dict):
        configured_backtest_file = _safe_str(config["backtest"].get("backtest_file"), "")
    selected_backtest_file = backtest_file or configured_backtest_file or None

    end_ts = int(time.time())
    start_ts = end_ts - (days * 24 * 60 * 60)

    try:
        if selected_backtest_file:
            markets, source = _load_backtest_markets(
                p=p,
                bt=bt,
                start_ts=start_ts,
                end_ts=end_ts,
                backtest_file=selected_backtest_file,
            )
        else:
            markets, source = _load_backtest_markets(
                p=p,
                bt=bt,
                start_ts=start_ts,
                end_ts=end_ts,
            )
    except Exception as exc:
        return {
            "status": "error",
            "error_code": "backtest_data_load_failed",
            "message": str(exc),
            "disclaimer": DISCLAIMER,
            "dry_run": True,
        }

    if not markets:
        return {
            "status": "error",
            "error_code": "no_backtest_markets",
            "message": "No paired historical markets were available for backtest.",
            "disclaimer": DISCLAIMER,
            "dry_run": True,
        }

    return _optimize_backtest(
        config=config,
        markets=markets,
        source=source,
        days=days,
        start_ts=start_ts,
        end_ts=end_ts,
        skill_name="liquidity-paired-basis-maker",
    )


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


def _coerce_trade_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get("markets", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _build_live_trade_pair(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    *,
    event_id: str,
    now_ts: int,
    p: StrategyParams,
) -> dict[str, Any]:
    end_ts = min(
        _safe_int(primary.get("end_ts"), now_ts + 86400),
        _safe_int(secondary.get("end_ts"), now_ts + 86400),
    )
    basis_volatility_bps = abs(
        (_safe_float(primary.get("mid_price"), 0.0) - _safe_float(secondary.get("mid_price"), 0.0))
        * 10000.0
    )
    return {
        "market_id": _safe_str(primary.get("market_id"), "unknown"),
        "pair_market_id": _safe_str(secondary.get("market_id"), "unknown"),
        "question": _safe_str(primary.get("question"), _safe_str(primary.get("market_id"), "unknown")),
        "pair_question": _safe_str(secondary.get("question"), _safe_str(secondary.get("market_id"), "unknown")),
        "event_id": event_id,
        "end_ts": end_ts,
        "seconds_to_resolution": max(0, end_ts - now_ts),
        "rebate_bps": (
            _safe_float(primary.get("rebate_bps"), p.maker_rebate_bps)
            + _safe_float(secondary.get("rebate_bps"), p.maker_rebate_bps)
        )
        / 2.0,
        "mid_price": round(_safe_float(primary.get("mid_price"), 0.0), 6),
        "pair_mid_price": round(_safe_float(secondary.get("mid_price"), 0.0), 6),
        "basis_volatility_bps": round(basis_volatility_bps, 3),
        "source": "live-seren-publisher",
    }


def _pair_live_trade_candidates(
    candidates: list[dict[str, Any]],
    *,
    now_ts: int,
    p: StrategyParams,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        grouped[_safe_str(row.get("event_id"), "misc")].append(row)

    pairs: list[dict[str, Any]] = []
    for event_id, group in grouped.items():
        if len(group) < 2:
            continue
        group_sorted = sorted(group, key=lambda row: _safe_float(row.get("volume24hr"), 0.0), reverse=True)
        for i in range(len(group_sorted) - 1):
            pairs.append(
                _build_live_trade_pair(
                    primary=group_sorted[i],
                    secondary=group_sorted[i + 1],
                    event_id=event_id,
                    now_ts=now_ts,
                    p=p,
                )
            )

    if pairs:
        return pairs

    fallback_sorted = sorted(candidates, key=lambda row: _safe_float(row.get("volume24hr"), 0.0), reverse=True)
    for i in range(0, len(fallback_sorted) - 1, 2):
        pairs.append(
            _build_live_trade_pair(
                primary=fallback_sorted[i],
                secondary=fallback_sorted[i + 1],
                event_id="fallback",
                now_ts=now_ts,
                p=p,
            )
        )
    return pairs


def _fetch_live_trade_pairs(config: dict[str, Any]) -> list[dict[str, Any]]:
    p = to_strategy_params(config)
    bt = to_backtest_params(config)
    now_ts = int(time.time())
    offset = 0
    candidates: list[dict[str, Any]] = []
    seen_token_ids: set[str] = set()
    pages = 0

    while True:
        pages += 1
        if pages > 200:
            break
        query = urlencode(
            {
                "active": "true",
                "closed": "false",
                "limit": bt.markets_fetch_page_size,
                "offset": offset,
                "order": "volume24hr",
                "ascending": "false",
            }
        )
        raw = _http_get_json(f"{bt.gamma_markets_url}?{query}")
        if not isinstance(raw, list) or not raw:
            break

        added_on_page = 0
        for market in raw:
            if not isinstance(market, dict):
                continue
            liquidity = _safe_float(market.get("liquidity"), 0.0)
            if liquidity < bt.min_liquidity_usd:
                continue

            end_ts = (
                _parse_iso_ts(market.get("endDate"))
                or _parse_iso_ts(market.get("endDateIso"))
                or _safe_int(market.get("end_ts"), now_ts + 86400)
            )
            seconds_to_resolution = max(0, end_ts - now_ts)
            if seconds_to_resolution < p.min_seconds_to_resolution:
                continue

            token_ids = _json_to_list(market.get("clobTokenIds"))
            if not token_ids:
                continue
            token_id = _safe_str(token_ids[0], "")
            if not token_id or token_id in seen_token_ids:
                continue
            seen_token_ids.add(token_id)

            mid_price = _extract_live_mid_price(market)
            if not (0.01 < mid_price < 0.99):
                continue

            events = _json_to_list(market.get("events"))
            event_id = ""
            if events and isinstance(events[0], dict):
                event_id = _safe_str(events[0].get("id"), "")
            if not event_id:
                event_id = _safe_str(market.get("seriesSlug"), "")
            if not event_id:
                event_id = _safe_str(market.get("category"), "misc")

            market_id = _safe_str(market.get("id"), _safe_str(market.get("conditionId"), token_id))
            candidates.append(
                {
                    "market_id": market_id,
                    "question": _safe_str(market.get("question"), market_id),
                    "token_id": token_id,
                    "event_id": event_id,
                    "end_ts": end_ts,
                    "seconds_to_resolution": seconds_to_resolution,
                    "rebate_bps": _safe_float(market.get("rebate_bps"), p.maker_rebate_bps),
                    "volume24hr": _safe_float(market.get("volume24hr"), 0.0),
                    "mid_price": mid_price,
                }
            )
            added_on_page += 1

        if added_on_page == 0:
            break
        offset += len(raw)
        if len(raw) < bt.markets_fetch_page_size:
            break

    candidates_with_cap = candidates[: bt.max_markets] if bt.max_markets > 0 else candidates
    return _pair_live_trade_candidates(candidates_with_cap, now_ts=now_ts, p=p)


def _load_trade_markets(config: dict[str, Any], markets_file: str | None) -> list[dict[str, Any]]:
    if markets_file:
        return _coerce_trade_rows(load_json(Path(markets_file)))
    configured_markets = _coerce_trade_rows(config.get("markets", []))
    if configured_markets:
        return configured_markets
    return _fetch_live_trade_pairs(config)


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
    bt = to_backtest_params(config)
    try:
        markets = _load_trade_markets(config, markets_file)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        return {
            "status": "error",
            "skill": "liquidity-paired-basis-maker",
            "error_code": "trade_market_load_failed",
            "message": str(exc),
            "hint": (
                "Provide --markets-file with a saved trade market snapshot if "
                "live market discovery is unavailable."
            ),
            "dry_run": True,
            "disclaimer": DISCLAIMER,
        }
    market_source = "config"
    if bool(execution.get("prefer_live_market_data", live_mode)):
        try:
            live_markets = load_live_pair_markets(
                pairs_max=p.pairs_max,
                min_seconds_to_resolution=p.min_seconds_to_resolution,
                min_history_points=max(24, bt.min_history_points),
                min_liquidity_usd=bt.min_liquidity_usd,
                markets_fetch_page_size=max(bt.markets_fetch_page_size, p.pairs_max * 8, 40),
                max_markets=bt.max_markets if bt.max_markets > 0 else max(p.pairs_max * 8, 40),
                history_interval=bt.history_interval,
                history_fidelity_minutes=bt.history_fidelity_minutes,
                default_rebate_bps=p.maker_rebate_bps,
            )
        except Exception as exc:
            if not markets:
                return {
                    "status": "error",
                    "error_code": "live_market_data_load_failed",
                    "message": str(exc),
                    "dry_run": True,
                    "disclaimer": DISCLAIMER,
                }
            live_markets = []
        if live_markets:
            markets = live_markets
            market_source = "live-seren-publisher"

    exposure = config.get("state", {}).get("leg_exposure", {})
    leg_exposure = {str(k): _safe_float(v, 0.0) for k, v in exposure.items()}
    raw_positions: Any = []
    live_trader: DirectClobTrader | None = None
    if live_mode:
        try:
            live_trader = DirectClobTrader(
                skill_root=Path(__file__).resolve().parents[1],
                client_name="liquidity-paired-basis-maker",
            )
            raw_positions = live_trader.get_positions()
            leg_exposure = pair_leg_exposure_notional(
                raw_positions=raw_positions,
                markets=markets,
            )
        except Exception as exc:
            return {
                "status": "error",
                "error_code": "live_execution_init_failed",
                "message": str(exc),
                "dry_run": True,
                "disclaimer": DISCLAIMER,
            }

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
    payload: dict[str, Any] = {
        "status": "ok",
        "skill": "liquidity-paired-basis-maker",
        "mode": mode,
        "dry_run": mode != "live",
        "market_source": market_source,
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
        live_execution = execute_pair_trades(
            trader=live_trader,
            pair_trades=trades,
            markets=markets,
            execution_settings=execution_settings,
        )
        payload["live_execution"] = live_execution
        payload["state"] = {
            "leg_exposure": live_execution.get("updated_leg_exposure", {}),
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
        covered_tokens = {
            _safe_str(o.get("token_id"), "")
            for o in live_execution.get("orders_submitted", [])
            if _safe_str(o.get("side"), "") == "SELL"
        }
        covered_tokens.discard("")
        unwind_sells = sell_held_inventory(
            trader=live_trader,
            raw_positions=raw_positions,
            covered_token_ids=covered_tokens,
        )
        if unwind_sells:
            payload["live_execution"]["held_inventory_unwinds"] = unwind_sells
            payload["strategy_summary"]["held_inventory_sells"] = len(unwind_sells)
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
            client_name="liquidity-paired-basis-maker",
        )
    except Exception as exc:
        return {"status": "error", "error_code": "trader_init_failed", "message": str(exc)}
    results: dict[str, Any] = {"status": "ok", "skill": "liquidity-paired-basis-maker", "mode": "unwind-all"}
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
                response = trader.create_order(token_id=token_id, side="SELL", price=sell_price, size=shares, tick_size="0.01", neg_risk=False, fee_rate_bps=0)
                sell_results.append({"token_id": token_id, "shares": shares, "price": sell_price, "response": response})
            except Exception as sell_exc:
                sell_results.append({"token_id": token_id, "shares": shares, "error": str(sell_exc)})
        results["sell_results"] = sell_results
        results["positions_unwound"] = len(sell_results)
    except Exception as exc:
        results["position_error"] = str(exc)
    return results


def main() -> int:
    args = parse_args()
    config = load_config(args.config)

    if args.unwind_all:
        if not args.yes_live:
            result = {"status": "error", "error_code": "unwind_confirmation_required", "message": "Emergency unwind requires --yes-live."}
        else:
            result = run_unwind_all(config=config)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get("status") == "ok" else 1

    backtest = run_backtest(
        config=config,
        backtest_days=args.backtest_days,
        backtest_file=args.backtest_file,
    )
    if backtest.get("status") != "ok":
        print(json.dumps(backtest, sort_keys=True))
        return 1

    if isinstance(backtest.get("config_updates"), dict):
        _apply_config_updates_in_place(config, backtest["config_updates"])
        try:
            _write_config(args.config, config)
        except Exception as exc:  # pragma: no cover - defensive runtime path
            backtest["config_writeback_warning"] = str(exc)

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
    if isinstance(trade.get("state"), dict):
        try:
            _persist_runtime_state(args.config, config, trade["state"])
        except Exception as exc:  # pragma: no cover - defensive runtime path
            trade["state_writeback_warning"] = str(exc)
    payload = {
        "status": "ok" if ok else "error",
        "skill": "liquidity-paired-basis-maker",
        "run_type": "trade",
        "backtest": backtest,
        "trade": trade,
        "disclaimer": DISCLAIMER,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
