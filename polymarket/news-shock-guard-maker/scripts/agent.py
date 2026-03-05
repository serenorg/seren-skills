#!/usr/bin/env python3
"""News-shock guarded maker scaffold for Polymarket binary markets."""

from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DISCLAIMER = (
    "This strategy can lose money, especially during sudden information shocks. "
    "Backtests are hypothetical and do not guarantee future performance. "
    "Use dry-run first and only trade with capital you can afford to lose."
)


@dataclass(frozen=True)
class StrategyParams:
    bankroll_usd: float = 500.0
    markets_max: int = 10
    min_seconds_to_resolution: int = 60 * 60
    min_edge_bps: float = 2.0
    maker_rebate_bps: float = 2.2
    expected_unwind_cost_bps: float = 1.5
    adverse_selection_bps: float = 1.4
    min_spread_bps: float = 20.0
    max_spread_bps: float = 180.0
    volatility_spread_multiplier: float = 0.4
    base_order_notional_usd: float = 50.0
    max_notional_per_market_usd: float = 230.0
    max_total_notional_usd: float = 500.0
    max_position_notional_usd: float = 200.0
    inventory_skew_strength_bps: float = 22.0
    shock_bps_threshold: float = 120.0
    shock_score_threshold: float = 0.65
    shock_cooldown_steps: int = 2
    shock_penalty_multiplier: float = 0.06


@dataclass(frozen=True)
class BacktestParams:
    days: int = 365
    days_min: int = 120
    days_max: int = 730
    participation_rate: float = 0.60
    volatility_window_points: int = 20
    min_history_points: int = 72
    min_events: int = 200
    min_liquidity_usd: float = 5000.0
    markets_fetch_page_size: int = 500
    max_markets: int = 0
    history_interval: str = "max"
    history_fidelity_minutes: int = 60
    gamma_markets_url: str = "https://gamma-api.polymarket.com/markets"
    clob_history_url: str = "https://clob.polymarket.com/prices-history"
    history_fetch_workers: int = 12


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run news-shock guarded maker strategy.")
    parser.add_argument("--config", default="config.json", help="Config file path.")
    parser.add_argument(
        "--run-type",
        default="backtest",
        choices=("backtest", "trade"),
        help="Run backtest only, or run trade mode after backtest gating.",
    )
    parser.add_argument("--markets-file", default=None, help="Optional trade market JSON file.")
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


def to_strategy_params(config: dict[str, Any]) -> StrategyParams:
    raw = config.get("strategy", {})
    return StrategyParams(
        bankroll_usd=max(1.0, _safe_float(raw.get("bankroll_usd"), 500.0)),
        markets_max=max(1, _safe_int(raw.get("markets_max"), 10)),
        min_seconds_to_resolution=max(60, _safe_int(raw.get("min_seconds_to_resolution"), 3600)),
        min_edge_bps=_safe_float(raw.get("min_edge_bps"), 2.0),
        maker_rebate_bps=_safe_float(raw.get("maker_rebate_bps"), 2.2),
        expected_unwind_cost_bps=_safe_float(raw.get("expected_unwind_cost_bps"), 1.5),
        adverse_selection_bps=_safe_float(raw.get("adverse_selection_bps"), 1.4),
        min_spread_bps=_safe_float(raw.get("min_spread_bps"), 20.0),
        max_spread_bps=_safe_float(raw.get("max_spread_bps"), 180.0),
        volatility_spread_multiplier=_safe_float(raw.get("volatility_spread_multiplier"), 0.4),
        base_order_notional_usd=max(1.0, _safe_float(raw.get("base_order_notional_usd"), 50.0)),
        max_notional_per_market_usd=max(
            1.0,
            _safe_float(raw.get("max_notional_per_market_usd"), 230.0),
        ),
        max_total_notional_usd=max(
            1.0,
            _safe_float(raw.get("max_total_notional_usd"), 500.0),
        ),
        max_position_notional_usd=max(
            1.0,
            _safe_float(raw.get("max_position_notional_usd"), 200.0),
        ),
        inventory_skew_strength_bps=max(
            0.0,
            _safe_float(raw.get("inventory_skew_strength_bps"), 22.0),
        ),
        shock_bps_threshold=max(20.0, _safe_float(raw.get("shock_bps_threshold"), 120.0)),
        shock_score_threshold=clamp(_safe_float(raw.get("shock_score_threshold"), 0.65), 0.0, 1.0),
        shock_cooldown_steps=max(0, _safe_int(raw.get("shock_cooldown_steps"), 2)),
        shock_penalty_multiplier=max(
            0.0,
            _safe_float(raw.get("shock_penalty_multiplier"), 0.06),
        ),
    )


def to_backtest_params(config: dict[str, Any]) -> BacktestParams:
    raw = config.get("backtest", {})
    range_raw = raw.get("days_range", {}) if isinstance(raw.get("days_range"), dict) else {}
    days_min = max(7, _safe_int(range_raw.get("min"), 120))
    days_max = max(days_min, _safe_int(range_raw.get("max"), 730))
    days = int(clamp(_safe_int(raw.get("days"), 365), days_min, days_max))
    return BacktestParams(
        days=days,
        days_min=days_min,
        days_max=days_max,
        participation_rate=clamp(_safe_float(raw.get("participation_rate"), 0.60), 0.0, 1.0),
        volatility_window_points=max(4, _safe_int(raw.get("volatility_window_points"), 20)),
        min_history_points=max(8, _safe_int(raw.get("min_history_points"), 72)),
        min_events=max(1, _safe_int(raw.get("min_events"), 200)),
        min_liquidity_usd=max(0.0, _safe_float(raw.get("min_liquidity_usd"), 5000.0)),
        markets_fetch_page_size=max(25, _safe_int(raw.get("markets_fetch_page_size"), 500)),
        max_markets=max(0, _safe_int(raw.get("max_markets"), 0)),
        history_interval=_safe_str(raw.get("history_interval"), "max"),
        history_fidelity_minutes=max(1, _safe_int(raw.get("history_fidelity_minutes"), 60)),
        gamma_markets_url=_safe_str(raw.get("gamma_markets_url"), "https://gamma-api.polymarket.com/markets"),
        clob_history_url=_safe_str(raw.get("clob_history_url"), "https://clob.polymarket.com/prices-history"),
        history_fetch_workers=max(1, _safe_int(raw.get("history_fetch_workers"), 12)),
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


def _parse_iso_ts(value: Any) -> int | None:
    raw = _safe_str(value, "")
    if not raw:
        return None
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _http_get_json(url: str, timeout: int = 30) -> dict[str, Any] | list[Any]:
    req = Request(
        url,
        headers={
            "User-Agent": "news-shock-guard-maker/1.1",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_live_backtest_markets(p: StrategyParams, bt: BacktestParams, start_ts: int, end_ts: int) -> list[dict[str, Any]]:
    offset = 0
    all_candidates: list[dict[str, Any]] = []
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

            market_id = _safe_str(market.get("id"), token_id)
            all_candidates.append(
                {
                    "market_id": market_id,
                    "question": _safe_str(market.get("question"), market_id),
                    "token_id": token_id,
                    "end_ts": end_market,
                    "rebate_bps": _safe_float(market.get("rebate_bps"), p.maker_rebate_bps),
                    "volume24hr": _safe_float(market.get("volume24hr"), 0.0),
                }
            )
            added_on_page += 1

        if added_on_page == 0:
            break
        offset += len(raw)
        if len(raw) < bt.markets_fetch_page_size:
            break

    candidates = all_candidates[: bt.max_markets] if bt.max_markets > 0 else all_candidates

    def _fetch_candidate_history(candidate: dict[str, Any]) -> dict[str, Any] | None:
        history_query = urlencode(
            {
                "market": candidate["token_id"],
                "interval": bt.history_interval,
                "fidelity": bt.history_fidelity_minutes,
            }
        )
        try:
            payload = _http_get_json(f"{bt.clob_history_url}?{history_query}")
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        history = _normalize_history(
            _json_to_list(payload.get("history")),
            start_ts=start_ts,
            end_ts=end_ts,
        )
        if len(history) < bt.min_history_points:
            return None
        return {
            "market_id": candidate["market_id"],
            "question": candidate["question"],
            "rebate_bps": candidate["rebate_bps"],
            "end_ts": candidate["end_ts"],
            "history": history,
            "volume24hr": candidate["volume24hr"],
            "source": "live-api",
        }

    selected: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, bt.history_fetch_workers)) as executor:
        futures = [executor.submit(_fetch_candidate_history, candidate) for candidate in candidates]
        for future in as_completed(futures):
            row = future.result()
            if row:
                selected.append(row)

    selected.sort(key=lambda row: _safe_float(row.get("volume24hr"), 0.0), reverse=True)
    for row in selected:
        row.pop("volume24hr", None)
    return selected


def _load_backtest_markets(
    p: StrategyParams,
    bt: BacktestParams,
    start_ts: int,
    end_ts: int,
) -> tuple[list[dict[str, Any]], str]:
    return _fetch_live_backtest_markets(p=p, bt=bt, start_ts=start_ts, end_ts=end_ts), "live-api"


def _max_drawdown_stats(equity_curve: list[float]) -> tuple[float, float]:
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
    return max_dd_usd, max_dd_pct


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


def _spread_bps(volatility_bps: float, p: StrategyParams) -> float:
    spread = p.min_spread_bps + (volatility_bps * p.volatility_spread_multiplier)
    return clamp(spread, p.min_spread_bps, p.max_spread_bps)


def _expected_edge_bps(spread_bps: float, rebate_bps: float, p: StrategyParams) -> float:
    return (
        (spread_bps / 2.0)
        + rebate_bps
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
            "considered_points": 0,
            "quoted_points": 0,
            "shock_skips": 0,
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
    shock_skips = 0
    filled_notional = 0.0
    pnl = 0.0
    event_pnls: list[float] = []
    cooldown = 0

    for idx in range(window, len(history) - 1):
        t, mid = history[idx]
        _, nxt = history[idx + 1]
        ttl = max(0, _safe_int(market.get("end_ts"), t + 86400) - t)
        if ttl < p.min_seconds_to_resolution or not (0.01 < mid < 0.99):
            continue

        considered += 1
        vol_bps = pstdev(moves_bps[idx - window : idx]) if window > 1 else p.min_spread_bps
        spread_bps = _spread_bps(vol_bps, p)
        expected_edge = _expected_edge_bps(spread_bps, rebate_bps, p)

        next_move_bps = abs((nxt - mid) * 10000.0)
        is_shock = next_move_bps >= p.shock_bps_threshold
        if is_shock:
            cooldown = p.shock_cooldown_steps

        if cooldown > 0 or is_shock:
            shock_skips += 1
            cooldown = max(0, cooldown - 1)
            continue

        if expected_edge < p.min_edge_bps:
            continue

        quoted += 1
        half_spread_bps = spread_bps / 2.0
        touch_ratio = min(1.0, next_move_bps / max(half_spread_bps, 1e-9))
        event_notional = p.base_order_notional_usd * bt.participation_rate * touch_ratio

        shock_penalty = next_move_bps * p.shock_penalty_multiplier
        pickoff_penalty = max(0.0, next_move_bps - half_spread_bps)
        realized_edge = expected_edge - shock_penalty - pickoff_penalty
        event_pnl = event_notional * realized_edge / 10000.0

        filled_notional += event_notional
        pnl += event_pnl
        event_pnls.append(event_pnl)

    return {
        "market_id": market["market_id"],
        "question": market["question"],
        "considered_points": considered,
        "quoted_points": quoted,
        "shock_skips": shock_skips,
        "filled_notional_usd": round(filled_notional, 4),
        "pnl_usd": round(pnl, 6),
        "event_pnls": event_pnls,
    }


def run_backtest(config: dict[str, Any], backtest_days: int | None) -> dict[str, Any]:
    p = to_strategy_params(config)
    bt = to_backtest_params(config)
    days = int(clamp(backtest_days if backtest_days is not None else bt.days, bt.days_min, bt.days_max))
    end_ts = int(time.time())
    start_ts = end_ts - (days * 24 * 60 * 60)

    try:
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
            "message": "No historical backtest markets were available.",
            "disclaimer": DISCLAIMER,
            "dry_run": True,
        }

    summaries: list[dict[str, Any]] = []
    event_pnls: list[float] = []
    considered = 0
    quoted = 0
    shock_skips = 0
    total_notional = 0.0

    for market in markets:
        result = _simulate_market(market, p, bt)
        summaries.append(
            {
                "market_id": result["market_id"],
                "question": result["question"],
                "considered_points": result["considered_points"],
                "quoted_points": result["quoted_points"],
                "shock_skips": result["shock_skips"],
                "filled_notional_usd": result["filled_notional_usd"],
                "pnl_usd": result["pnl_usd"],
            }
        )
        considered += int(result["considered_points"])
        quoted += int(result["quoted_points"])
        shock_skips += int(result["shock_skips"])
        total_notional += float(result["filled_notional_usd"])
        event_pnls.extend(result["event_pnls"])

    equity_curve = [p.bankroll_usd]
    equity = p.bankroll_usd
    for event_pnl in event_pnls:
        equity += event_pnl
        equity_curve.append(equity)

    total_pnl = equity - p.bankroll_usd
    total_return_pct = (total_pnl / p.bankroll_usd) * 100.0
    max_drawdown_usd, max_drawdown_pct = _max_drawdown_stats(equity_curve)
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
                "markets_loaded": len(markets),
                "events_observed": events,
                "min_events_required": bt.min_events,
            },
            "disclaimer": DISCLAIMER,
        }

    return {
        "status": "ok",
        "skill": "news-shock-guard-maker",
        "mode": "backtest",
        "dry_run": True,
        "backtest_summary": {
            "days": days,
            "days_range": {"min": bt.days_min, "max": bt.days_max},
            "start_utc": datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat(),
            "end_utc": datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat(),
            "source": source,
            "markets_selected": len(summaries),
            "considered_points": considered,
            "quoted_points": quoted,
            "shock_skips": shock_skips,
            "quote_rate_pct": round((quoted / considered) * 100.0 if considered else 0.0, 4),
        },
        "results": {
            "starting_bankroll_usd": round(p.bankroll_usd, 2),
            "ending_bankroll_usd": round(equity, 2),
            "total_pnl_usd": round(total_pnl, 4),
            "return_pct": round(total_return_pct, 4),
            "total_return_pct": round(total_return_pct, 4),
            "annualized_return_pct": round(annualized_return_pct, 4),
            "sharpe_like_score": round(sharpe_like, 4),
            "hit_rate_pct": round(hit_rate_pct, 4),
            "filled_notional_usd": round(total_notional, 2),
            "turnover_multiple": round(turnover_multiple, 4),
            "events": events,
            "min_events_required": bt.min_events,
            "max_drawdown_usd": round(max_drawdown_usd, 4),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
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
    ttl = max(0, _safe_int(market.get("seconds_to_resolution"), 0))
    volatility_bps = max(0.0, _safe_float(market.get("volatility_bps"), p.min_spread_bps))
    shock_score = clamp(_safe_float(market.get("news_shock_score"), 0.0), 0.0, 1.0)
    breaking_news = bool(market.get("breaking_news", False))

    if ttl < p.min_seconds_to_resolution:
        return {"market_id": market_id, "status": "skipped", "reason": "near_resolution"}
    if not (0.01 < mid < 0.99):
        return {"market_id": market_id, "status": "skipped", "reason": "extreme_probability"}
    if breaking_news or shock_score >= p.shock_score_threshold or volatility_bps >= p.shock_bps_threshold:
        return {
            "market_id": market_id,
            "status": "skipped",
            "reason": "news_shock_guard",
            "shock_score": round(shock_score, 4),
            "volatility_bps": round(volatility_bps, 3),
        }

    rebate_bps = _safe_float(market.get("rebate_bps"), p.maker_rebate_bps)
    spread_bps = _spread_bps(volatility_bps, p)
    edge_bps = _expected_edge_bps(spread_bps, rebate_bps, p)
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

    remaining_market = max(0.0, p.max_notional_per_market_usd - abs(inventory_notional))
    remaining_total = max(0.0, p.max_total_notional_usd - max(outstanding_notional, 0.0))
    quote_notional = min(p.base_order_notional_usd, remaining_market, remaining_total)
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
        "shock_score": round(shock_score, 4),
        "volatility_bps": round(volatility_bps, 3),
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
    inventory_map = config.get("state", {}).get("inventory", {})
    inventory = {str(k): _safe_float(v, 0.0) for k, v in inventory_map.items()}
    markets = _load_trade_markets(config, markets_file)

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
            quotes.append(proposal)
            outstanding_notional += float(proposal["quote_notional_usd"])
        else:
            skips.append(
                {
                    "market_id": market_id,
                    "reason": _safe_str(proposal.get("reason"), "unknown"),
                    "shock_score": proposal.get("shock_score"),
                    "volatility_bps": proposal.get("volatility_bps"),
                    "edge_bps": proposal.get("edge_bps"),
                }
            )

    mode = "live" if live_mode and yes_live and not dry_run else "dry-run"
    return {
        "status": "ok",
        "skill": "news-shock-guard-maker",
        "mode": mode,
        "dry_run": mode != "live",
        "strategy_summary": {
            "markets_considered": len(markets),
            "markets_quoted": len(quotes),
            "markets_skipped": len(skips),
            "outstanding_notional_usd": round(outstanding_notional, 2),
            "shock_guard_threshold_bps": p.shock_bps_threshold,
            "shock_guard_threshold_score": p.shock_score_threshold,
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
        "skill": "news-shock-guard-maker",
        "run_type": "trade",
        "backtest": backtest,
        "trade": trade,
        "disclaimer": DISCLAIMER,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
