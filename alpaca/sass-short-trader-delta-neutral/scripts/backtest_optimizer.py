from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


DEFAULT_BACKTEST_SETTINGS = {
    "auto_optimize_on_invoke": True,
    "bankroll_usd": 100.0,
    "target_pnl_pct": 25.0,
    "min_conviction_candidates": [55.0, 60.0, 65.0, 70.0],
    "max_names_scored_candidates": [20, 30],
    "max_names_orders_candidates": [2, 3, 5, 8],
    "hedge_ratio_candidates": [0.25, 0.5, 0.75, 1.0],
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
    return settings


def _modeled_pnl_pct(result: dict[str, Any], bankroll: float) -> float:
    sim = result.get("sim", {})
    net_pnl = float(sim.get("net_pnl_20d", 0.0))
    gross_exposure = float(sim.get("gross_exposure", 0.0))
    capital = gross_exposure if gross_exposure > 0 else bankroll
    return round((net_pnl / max(capital, 1e-9)) * 100.0, 4)


def optimize_scan_config(
    *,
    base_config: dict[str, Any],
    run_scan: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    settings = resolve_backtest_settings(base_config)
    if not settings["auto_optimize_on_invoke"]:
        return {
            "config": deepcopy(base_config),
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
            "result": None,
        }

    attempts = 0
    bankroll = max(float(settings["bankroll_usd"]), 1.0)
    best_attempt = None
    best_result = None

    for max_names_scored in settings.get("max_names_scored_candidates", []):
        for max_names_orders in settings.get("max_names_orders_candidates", []):
            for min_conviction in settings.get("min_conviction_candidates", []):
                for hedge_ratio in settings.get("hedge_ratio_candidates", []):
                    candidate = deepcopy(base_config)
                    candidate["portfolio_notional_usd"] = bankroll
                    candidate["max_names_scored"] = int(max_names_scored)
                    candidate["max_names_orders"] = int(max_names_orders)
                    candidate["min_conviction"] = float(min_conviction)
                    candidate["hedge_ratio"] = float(hedge_ratio)
                    result = run_scan(candidate)
                    attempts += 1
                    modeled_pnl_pct = _modeled_pnl_pct(result, bankroll)
                    record = {
                        "modeled_pnl_pct": modeled_pnl_pct,
                        "selected_targets": list(result.get("selected", [])),
                        "selected_config": {
                            "portfolio_notional_usd": round(bankroll, 2),
                            "max_names_scored": int(max_names_scored),
                            "max_names_orders": int(max_names_orders),
                            "min_conviction": float(min_conviction),
                            "hedge_ratio": float(hedge_ratio),
                        },
                    }
                    if best_attempt is None or modeled_pnl_pct > best_attempt["modeled_pnl_pct"]:
                        best_attempt = record
                        best_result = result

    if best_attempt is None:
        return {
            "config": deepcopy(base_config),
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
            "result": None,
        }

    updated = deepcopy(base_config)
    updated["portfolio_notional_usd"] = round(bankroll, 2)
    updated["max_names_scored"] = best_attempt["selected_config"]["max_names_scored"]
    updated["max_names_orders"] = best_attempt["selected_config"]["max_names_orders"]
    updated["min_conviction"] = best_attempt["selected_config"]["min_conviction"]
    updated["hedge_ratio"] = best_attempt["selected_config"]["hedge_ratio"]
    updated["backtest"] = _deep_merge(
        settings,
        {
            "selected_config": best_attempt["selected_config"],
            "selected_targets": best_attempt["selected_targets"],
            "last_modeled_pnl_pct": best_attempt["modeled_pnl_pct"],
            "last_attempt_count": attempts,
            "last_target_met": best_attempt["modeled_pnl_pct"] >= float(settings["target_pnl_pct"]),
        },
    )
    summary = {
        "applied": True,
        "bankroll_usd": round(bankroll, 2),
        "target_pnl_pct": float(settings["target_pnl_pct"]),
        "target_met": best_attempt["modeled_pnl_pct"] >= float(settings["target_pnl_pct"]),
        "attempt_count": attempts,
        "modeled_pnl_pct": best_attempt["modeled_pnl_pct"],
        "selected_config": best_attempt["selected_config"],
        "selected_targets": best_attempt["selected_targets"],
    }
    return {"config": updated, "summary": summary, "result": best_result}
