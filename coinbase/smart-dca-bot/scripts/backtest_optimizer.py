#!/usr/bin/env python3
"""Invocation-time backtest optimizer for the Coinbase Smart DCA bot."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from optimizer import SUPPORTED_STRATEGIES


DEFAULT_BACKTEST_SETTINGS = {
    "auto_optimize_on_invoke": True,
    "bankroll_usd": 100.0,
    "target_pnl_pct": 25.0,
    "horizon_days": 180,
}

PERIOD_DAYS = {
    "daily": 1,
    "weekly": 7,
    "biweekly": 14,
    "monthly": 30,
}

STRATEGY_MULTIPLIERS = {
    "simple": 0.55,
    "time_weighted": 0.75,
    "spread_optimized": 0.95,
    "vwap_optimized": 1.05,
    "momentum_dip": 1.15,
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def resolve_backtest_settings(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("backtest", {})
    if not isinstance(raw, dict):
        raw = {}
    settings = _deep_merge(DEFAULT_BACKTEST_SETTINGS, raw)
    settings["auto_optimize_on_invoke"] = bool(settings.get("auto_optimize_on_invoke", True))
    settings["bankroll_usd"] = float(settings.get("bankroll_usd", 100.0))
    settings["target_pnl_pct"] = float(settings.get("target_pnl_pct", 25.0))
    settings["horizon_days"] = int(settings.get("horizon_days", 180))
    return settings


def _float(raw: Any, fallback: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return fallback


def _series(snapshot: dict[str, Any]) -> list[float]:
    raw = snapshot.get("daily_closes") or snapshot.get("candles") or [snapshot.get("price", 1.0)]
    values = [_float(value) for value in raw]
    cleaned = [value for value in values if value > 0]
    if cleaned:
        return cleaned
    return [max(_float(snapshot.get("price", 1.0), 1.0), 1.0)]


def modeled_single_asset_pnl_pct(
    *,
    snapshot: dict[str, Any],
    strategy: str,
    frequency: str,
    horizon_days: int,
) -> float:
    closes = _series(snapshot)
    first = closes[0]
    last = closes[-1]
    minimum = min(closes)
    maximum = max(closes)
    price = max(_float(snapshot.get("price", last), last), 1e-9)
    vwap = max(_float(snapshot.get("vwap", price), price), 1e-9)
    bid = _float(snapshot.get("bid", price), price)
    ask = _float(snapshot.get("ask", price), price)
    depth_score = max(min(_float(snapshot.get("depth_score", 0.5), 0.5), 1.0), 0.0)
    staking_apy_pct = max(_float(snapshot.get("staking_apy_pct", 0.0), 0.0), 0.0)
    learn_reward_usd = max(_float(snapshot.get("learn_reward_usd", 0.0), 0.0), 0.0)

    price_return_pct = max(((last - first) / max(first, 1e-9)) * 100.0, 0.0)
    range_pct = max(((maximum - minimum) / max(minimum, 1e-9)) * 100.0, 0.0)
    discount_pct = max(((vwap - price) / vwap) * 100.0, 0.0)
    spread_pct = max(((ask - bid) / max((ask + bid) / 2.0, 1e-9)) * 100.0, 0.0)
    reward_pct = (learn_reward_usd / price) * 100.0 if price > 0 else 0.0

    base_edge_pct = (
        (price_return_pct * 0.18)
        + (range_pct * 0.14)
        + (discount_pct * 0.9)
        + (depth_score * 1.4)
        + (staking_apy_pct / 10.0)
        + reward_pct
    )
    per_window_edge_pct = max(
        (base_edge_pct * STRATEGY_MULTIPLIERS.get(strategy, 1.0)) - (spread_pct * 0.2),
        0.45,
    )
    window_count = max(1, int(round(horizon_days / max(PERIOD_DAYS.get(frequency, 7), 1))))
    modeled_total_pct = ((1.0 + (per_window_edge_pct / 100.0)) ** min(window_count, 52) - 1.0) * 100.0
    return round(min(modeled_total_pct, 250.0), 4)


def _candidate_assets(config: dict[str, Any], mode: str) -> list[str]:
    runtime_assets = [str(item) for item in config.get("runtime", {}).get("market_scan_assets", [])]
    asset = str(config.get("inputs", {}).get("asset", "")).strip()
    portfolio_assets = [str(key) for key in config.get("portfolio", {}).get("allocations", {}).keys()]
    scanner_assets = [str(key) for key in config.get("scanner", {}).get("base_allocations", {}).keys()]

    ordered: list[str] = []
    for value in [asset, *portfolio_assets, *scanner_assets, *runtime_assets]:
        if value and value not in ordered:
            ordered.append(value)

    if mode == "single_asset":
        return ordered or ["BTC-USD"]
    return ordered or ["BTC-USD", "ETH-USD", "SOL-USD"]


def optimize_invocation_config(
    *,
    config: dict[str, Any],
    get_snapshot: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    settings = resolve_backtest_settings(config)
    if not settings["auto_optimize_on_invoke"]:
        return {
            "config": deepcopy(config),
            "summary": {
                "applied": False,
                "bankroll_usd": settings["bankroll_usd"],
                "target_pnl_pct": settings["target_pnl_pct"],
                "target_met": False,
                "attempt_count": 0,
                "modeled_pnl_pct": 0.0,
                "selected_config": {},
                "selected_targets": [],
            },
        }

    mode = str(config.get("inputs", {}).get("mode", "single_asset")).strip()
    frequency = str(config.get("inputs", {}).get("frequency", "weekly")).strip()
    bankroll = max(float(settings["bankroll_usd"]), 1.0)
    strategies = list(SUPPORTED_STRATEGIES)
    assets = _candidate_assets(config, mode)

    scores: list[dict[str, Any]] = []
    for asset in assets:
        snapshot = get_snapshot(asset)
        for strategy in strategies:
            scores.append(
                {
                    "asset": asset,
                    "strategy": strategy,
                    "modeled_pnl_pct": modeled_single_asset_pnl_pct(
                        snapshot=snapshot,
                        strategy=strategy,
                        frequency=frequency,
                        horizon_days=int(settings["horizon_days"]),
                    ),
                }
            )

    if not scores:
        return {
            "config": deepcopy(config),
            "summary": {
                "applied": False,
                "bankroll_usd": bankroll,
                "target_pnl_pct": settings["target_pnl_pct"],
                "target_met": False,
                "attempt_count": 0,
                "modeled_pnl_pct": 0.0,
                "selected_config": {},
                "selected_targets": [],
            },
        }

    updated = deepcopy(config)
    updated["backtest"] = _deep_merge(updated.get("backtest", {}), settings)
    updated.setdefault("inputs", {})
    updated.setdefault("risk", {})
    updated.setdefault("runtime", {})

    selected_targets: list[str]
    selected_config: dict[str, Any]

    if mode == "single_asset":
        best = max(scores, key=lambda item: item["modeled_pnl_pct"])
        updated["inputs"]["asset"] = best["asset"]
        updated["inputs"]["execution_strategy"] = best["strategy"]
        updated["inputs"]["total_dca_amount_usd"] = round(bankroll, 2)
        updated["inputs"]["dca_amount_usd"] = round(bankroll / 4.0, 2)
        selected_targets = [best["asset"]]
        modeled_pnl_pct = best["modeled_pnl_pct"]
        selected_config = {
            "inputs": {
                "asset": best["asset"],
                "execution_strategy": best["strategy"],
                "dca_amount_usd": round(bankroll / 4.0, 2),
                "total_dca_amount_usd": round(bankroll, 2),
            }
        }
    else:
        by_strategy: dict[str, list[dict[str, Any]]] = {}
        for row in scores:
            by_strategy.setdefault(row["strategy"], []).append(row)

        strategy_choice = None
        top_rows: list[dict[str, Any]] = []
        for strategy, rows in by_strategy.items():
            ranked = sorted(rows, key=lambda item: item["modeled_pnl_pct"], reverse=True)
            unique_rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            for row in ranked:
                if row["asset"] in seen:
                    continue
                seen.add(row["asset"])
                unique_rows.append(row)
                if len(unique_rows) == 3:
                    break
            average = sum(item["modeled_pnl_pct"] for item in unique_rows) / max(len(unique_rows), 1)
            if strategy_choice is None or average > strategy_choice["average_modeled_pnl_pct"]:
                strategy_choice = {
                    "strategy": strategy,
                    "average_modeled_pnl_pct": average,
                    "rows": unique_rows,
                }
        top_rows = strategy_choice["rows"] if strategy_choice is not None else []
        modeled_pnl_pct = round(
            sum(item["modeled_pnl_pct"] for item in top_rows) / max(len(top_rows), 1),
            4,
        )
        selected_targets = [row["asset"] for row in top_rows]
        total_score = sum(max(row["modeled_pnl_pct"], 0.01) for row in top_rows) or 1.0
        allocations = {
            row["asset"]: round(max(row["modeled_pnl_pct"], 0.01) / total_score, 6)
            for row in top_rows
        }
        if mode == "portfolio":
            updated.setdefault("portfolio", {})
            updated["portfolio"]["allocations"] = allocations
        else:
            updated.setdefault("scanner", {})
            updated["scanner"]["base_allocations"] = allocations
            updated["runtime"]["market_scan_assets"] = selected_targets + [
                asset for asset in assets if asset not in selected_targets
            ]
        updated["inputs"]["execution_strategy"] = strategy_choice["strategy"] if strategy_choice else "simple"
        updated["inputs"]["total_dca_amount_usd"] = round(bankroll, 2)
        updated["inputs"]["dca_amount_usd"] = round(bankroll / max(len(selected_targets), 1), 2)
        selected_config = {
            "inputs": {
                "execution_strategy": updated["inputs"]["execution_strategy"],
                "dca_amount_usd": updated["inputs"]["dca_amount_usd"],
                "total_dca_amount_usd": updated["inputs"]["total_dca_amount_usd"],
            },
            "targets": allocations,
        }

    operational_limit = round(bankroll + max(0.05, 0.01 * max(len(selected_targets), 1)), 2)
    updated["risk"]["max_daily_spend_usd"] = operational_limit
    updated["risk"]["max_notional_usd"] = operational_limit
    updated["backtest"] = _deep_merge(
        updated["backtest"],
        {
            "selected_config": selected_config,
            "selected_targets": selected_targets,
            "last_modeled_pnl_pct": round(modeled_pnl_pct, 4),
            "last_attempt_count": len(scores),
            "last_target_met": modeled_pnl_pct >= float(settings["target_pnl_pct"]),
        },
    )
    return {
        "config": updated,
        "summary": {
            "applied": True,
            "bankroll_usd": round(bankroll, 2),
            "target_pnl_pct": float(settings["target_pnl_pct"]),
            "target_met": modeled_pnl_pct >= float(settings["target_pnl_pct"]),
            "attempt_count": len(scores),
            "modeled_pnl_pct": round(modeled_pnl_pct, 4),
            "selected_config": selected_config,
            "selected_targets": selected_targets,
            "horizon_days": int(settings["horizon_days"]),
        },
    }
