#!/usr/bin/env python3
"""SkillForge-generated runtime scaffold for Kraken Smart DCA Bot.

This runtime intentionally defaults to dry-run mode and does not execute
network calls. It provides deterministic planning logic, guardrails, and
run persistence that can be extended with direct Kraken API integration.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SKILL_NAME = "smart-dca-bot"
DEFAULT_CONFIG_PATH = "config.json"
DEFAULT_DRY_RUN = True
STATE_DB_PATH = Path("state/dca_runs.db")

SUPPORTED_MODES = {"single_asset", "portfolio", "opportunity_scanner"}
SUPPORTED_FREQUENCIES = {"daily", "weekly", "biweekly", "monthly"}
SUPPORTED_RISK_LEVELS = {"conservative", "moderate", "aggressive"}

RISK_DISCLAIMER = (
    "Cryptocurrency trading involves substantial risk of loss and this skill "
    "does not provide financial advice."
)


@dataclass
class RuntimeInputs:
    mode: str
    asset: str
    amount_usd: float
    frequency: str
    execution_window_hours: int
    opportunity_allocation_pct: float
    risk_level: str
    auto_execute: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Kraken Smart DCA Bot runtime scaffold.")
    parser.add_argument(
        "--config",
        default=DEFAULT_CONFIG_PATH,
        help="Path to runtime config file (default: config.json).",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Allow live mode when config sets dry_run=false.",
    )
    parser.add_argument(
        "--accept-risk-disclaimer",
        action="store_true",
        help="Acknowledge the risk disclaimer required for live mode.",
    )
    return parser.parse_args()


def load_config(path: str) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def _default_inputs() -> RuntimeInputs:
    return RuntimeInputs(
        mode="single_asset",
        asset="BTC",
        amount_usd=50.0,
        frequency="weekly",
        execution_window_hours=24,
        opportunity_allocation_pct=20.0,
        risk_level="moderate",
        auto_execute=False,
    )


def parse_inputs(config: dict[str, Any]) -> RuntimeInputs:
    defaults = _default_inputs()
    raw = config.get("inputs", {})
    return RuntimeInputs(
        mode=str(raw.get("mode", defaults.mode)),
        asset=str(raw.get("asset", defaults.asset)).upper(),
        amount_usd=float(raw.get("amount_usd", defaults.amount_usd)),
        frequency=str(raw.get("frequency", defaults.frequency)),
        execution_window_hours=int(
            raw.get("execution_window_hours", defaults.execution_window_hours)
        ),
        opportunity_allocation_pct=float(
            raw.get("opportunity_allocation_pct", defaults.opportunity_allocation_pct)
        ),
        risk_level=str(raw.get("risk_level", defaults.risk_level)),
        auto_execute=bool(raw.get("auto_execute", defaults.auto_execute)),
    )


def validate_inputs(inputs: RuntimeInputs) -> list[str]:
    errors: list[str] = []
    if inputs.mode not in SUPPORTED_MODES:
        errors.append(f"Unsupported mode '{inputs.mode}'.")
    if inputs.frequency not in SUPPORTED_FREQUENCIES:
        errors.append(f"Unsupported frequency '{inputs.frequency}'.")
    if inputs.risk_level not in SUPPORTED_RISK_LEVELS:
        errors.append(f"Unsupported risk level '{inputs.risk_level}'.")
    if inputs.amount_usd <= 0:
        errors.append("amount_usd must be > 0.")
    if not (1 <= inputs.execution_window_hours <= 72):
        errors.append("execution_window_hours must be between 1 and 72.")
    if not (0 <= inputs.opportunity_allocation_pct <= 40):
        errors.append("opportunity_allocation_pct must be between 0 and 40.")
    return errors


def collect_market_snapshot(asset: str) -> dict[str, Any]:
    # Deterministic placeholder snapshot; replace with direct Kraken market data calls.
    basis = sum(ord(char) for char in asset)
    volatility = 0.015 + (basis % 10) / 1000.0
    liquidity = 0.60 + (basis % 7) / 20.0
    intraday_timing = 0.45 + (basis % 11) / 50.0
    return {
        "asset": asset,
        "volatility": round(volatility, 4),
        "liquidity": round(min(liquidity, 0.99), 4),
        "timing": round(min(intraday_timing, 0.99), 4),
        "captured_at": datetime.now(tz=UTC).isoformat(),
    }


def score_entry_window(snapshot: dict[str, Any], risk_level: str) -> float:
    # Lower volatility and higher liquidity/timing produce better entry scores.
    risk_modifier = {
        "conservative": 0.95,
        "moderate": 1.00,
        "aggressive": 1.10,
    }[risk_level]
    base_score = (
        (1.0 - snapshot["volatility"]) * 0.35
        + snapshot["liquidity"] * 0.30
        + snapshot["timing"] * 0.35
    )
    return round(min(base_score * risk_modifier, 1.0), 4)


def default_portfolio_allocations() -> list[dict[str, Any]]:
    return [
        {"asset": "BTC", "target_pct": 60},
        {"asset": "ETH", "target_pct": 25},
        {"asset": "SOL", "target_pct": 15},
    ]


def create_execution_plan(
    *,
    inputs: RuntimeInputs,
    entry_score: float,
    dry_run: bool,
) -> dict[str, Any]:
    threshold = 0.62
    plan: dict[str, Any] = {
        "mode": inputs.mode,
        "entry_score": entry_score,
        "execute_now": entry_score >= threshold,
        "dry_run": dry_run,
        "orders": [],
    }

    if inputs.mode == "single_asset":
        plan["orders"].append(
            {
                "asset": inputs.asset,
                "side": "buy",
                "notional_usd": round(inputs.amount_usd, 2),
                "reason": "single_asset_dca",
            }
        )
        return plan

    if inputs.mode == "portfolio":
        for allocation in default_portfolio_allocations():
            notional = inputs.amount_usd * (allocation["target_pct"] / 100)
            plan["orders"].append(
                {
                    "asset": allocation["asset"],
                    "side": "buy",
                    "notional_usd": round(notional, 2),
                    "target_pct": allocation["target_pct"],
                    "reason": "portfolio_dca",
                }
            )
        return plan

    opportunity_budget = inputs.amount_usd * (inputs.opportunity_allocation_pct / 100)
    base_budget = inputs.amount_usd - opportunity_budget
    plan["orders"].append(
        {
            "asset": inputs.asset,
            "side": "buy",
            "notional_usd": round(base_budget, 2),
            "reason": "base_dca",
        }
    )
    if entry_score >= threshold:
        plan["orders"].append(
            {
                "asset": "SOL",
                "side": "buy",
                "notional_usd": round(opportunity_budget, 2),
                "reason": "opportunity_shift",
                "requires_confirmation": not inputs.auto_execute,
            }
        )
    return plan


def enforce_live_guards(
    *,
    dry_run: bool,
    allow_live: bool,
    accepted_disclaimer: bool,
) -> None:
    if dry_run:
        return
    if not allow_live:
        raise ValueError("Live mode requested but --allow-live was not provided.")
    if not accepted_disclaimer:
        raise ValueError(
            "Live mode requested but --accept-risk-disclaimer was not provided. "
            f"Disclaimer: {RISK_DISCLAIMER}"
        )


def persist_run(*, inputs: RuntimeInputs, dry_run: bool, result: dict[str, Any]) -> None:
    STATE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(STATE_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              mode TEXT NOT NULL,
              asset TEXT NOT NULL,
              amount_usd REAL NOT NULL,
              dry_run INTEGER NOT NULL,
              entry_score REAL NOT NULL,
              status TEXT NOT NULL,
              details_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO runs (
              created_at, mode, asset, amount_usd, dry_run,
              entry_score, status, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(tz=UTC).isoformat(),
                inputs.mode,
                inputs.asset,
                inputs.amount_usd,
                int(dry_run),
                float(result.get("entry_score", 0.0)),
                result.get("status", "ok"),
                json.dumps(result, sort_keys=True),
            ),
        )
        conn.commit()


def run_once(*, config: dict[str, Any], allow_live: bool, accept_risk_disclaimer: bool) -> dict[str, Any]:
    dry_run = bool(config.get("dry_run", DEFAULT_DRY_RUN))
    enforce_live_guards(
        dry_run=dry_run,
        allow_live=allow_live,
        accepted_disclaimer=accept_risk_disclaimer,
    )

    inputs = parse_inputs(config)
    errors = validate_inputs(inputs)
    if errors:
        result = {
            "status": "error",
            "skill": SKILL_NAME,
            "error_code": "validation_error",
            "errors": errors,
        }
        persist_run(inputs=inputs, dry_run=dry_run, result=result)
        return result

    snapshot = collect_market_snapshot(inputs.asset)
    entry_score = score_entry_window(snapshot, inputs.risk_level)
    plan = create_execution_plan(inputs=inputs, entry_score=entry_score, dry_run=dry_run)

    result = {
        "status": "ok",
        "skill": SKILL_NAME,
        "execution_model": "local_direct_kraken_api",
        "dry_run": dry_run,
        "mode": inputs.mode,
        "frequency": inputs.frequency,
        "entry_score": entry_score,
        "market_snapshot": snapshot,
        "plan": plan,
        "risk_disclaimer": RISK_DISCLAIMER,
    }
    persist_run(inputs=inputs, dry_run=dry_run, result=result)
    return result


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    result = run_once(
        config=config,
        allow_live=args.allow_live,
        accept_risk_disclaimer=args.accept_risk_disclaimer,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
