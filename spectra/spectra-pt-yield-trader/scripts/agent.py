#!/usr/bin/env python3
"""SkillForge runtime for Spectra PT yield planning and execution handoff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import os
import sys
from urllib.request import Request, urlopen


DEFAULT_DRY_RUN = True
SUPPORTED_CHAINS = {
    "ethereum",
    "base",
    "arbitrum",
    "optimism",
    "avalanche",
    "bsc",
    "sonic",
    "flare",
    "katana",
    "monad",
}


class ConfigError(Exception):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Spectra PT yield planner runtime.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to runtime config file (default: config.json).",
    )
    return parser.parse_args()


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid config JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigError("Config root must be a JSON object.")
    return parsed


def _as_number(raw: object, *, field: str) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    raise ConfigError(f"{field} must be numeric.")


def _as_int(raw: object, *, field: str) -> int:
    if isinstance(raw, bool):
        raise ConfigError(f"{field} must be an integer.")
    if isinstance(raw, int):
        return raw
    raise ConfigError(f"{field} must be an integer.")


def _resolve_inputs(config: dict) -> dict:
    inputs = config.get("inputs", {})
    if not isinstance(inputs, dict):
        raise ConfigError("inputs must be an object.")

    chain = str(inputs.get("chain", "base")).lower()
    if chain not in SUPPORTED_CHAINS:
        raise ConfigError(f"Unsupported chain '{chain}'.")

    wallet_mode = str(inputs.get("wallet_mode", "delegated")).lower()
    if wallet_mode != "delegated":
        raise ConfigError("wallet_mode must be 'delegated'.")

    side = str(inputs.get("side", "buy")).lower()
    if side not in {"buy", "sell"}:
        raise ConfigError("side must be 'buy' or 'sell'.")

    capital_usd = _as_number(inputs.get("capital_usd", 100), field="inputs.capital_usd")
    if capital_usd <= 0:
        raise ConfigError("inputs.capital_usd must be > 0.")

    top_n = _as_int(inputs.get("top_n", 5), field="inputs.top_n")
    if top_n < 1 or top_n > 20:
        raise ConfigError("inputs.top_n must be in [1, 20].")

    min_liquidity_usd = _as_number(
        inputs.get("min_liquidity_usd", 50_000),
        field="inputs.min_liquidity_usd",
    )
    if min_liquidity_usd < 0:
        raise ConfigError("inputs.min_liquidity_usd must be >= 0.")

    max_price_impact_pct = _as_number(
        inputs.get("max_price_impact_pct", 2),
        field="inputs.max_price_impact_pct",
    )
    if max_price_impact_pct < 0 or max_price_impact_pct > 10:
        raise ConfigError("inputs.max_price_impact_pct must be in [0, 10].")

    maturity_min = _as_int(
        inputs.get("target_maturity_days_min", 7),
        field="inputs.target_maturity_days_min",
    )
    maturity_max = _as_int(
        inputs.get("target_maturity_days_max", 365),
        field="inputs.target_maturity_days_max",
    )
    if maturity_min < 0:
        raise ConfigError("inputs.target_maturity_days_min must be >= 0.")
    if maturity_max < 1:
        raise ConfigError("inputs.target_maturity_days_max must be >= 1.")
    if maturity_min > maturity_max:
        raise ConfigError("inputs target maturity min cannot exceed max.")

    include_looping = bool(inputs.get("include_looping", False))
    live_mode = bool(inputs.get("live_mode", False))

    return {
        "chain": chain,
        "wallet_mode": wallet_mode,
        "side": side,
        "capital_usd": capital_usd,
        "top_n": top_n,
        "underlying_symbol": str(inputs.get("underlying_symbol", "USDC")).upper(),
        "min_liquidity_usd": min_liquidity_usd,
        "max_price_impact_pct": max_price_impact_pct,
        "target_maturity_days_min": maturity_min,
        "target_maturity_days_max": maturity_max,
        "pt_address": str(inputs.get("pt_address", "")).strip(),
        "wallet_address": str(inputs.get("wallet_address", "")).strip(),
        "ve_spectra_balance": _as_number(
            inputs.get("ve_spectra_balance", 0),
            field="inputs.ve_spectra_balance",
        ),
        "include_looping": include_looping,
        "live_mode": live_mode,
    }


def _resolve_policies(config: dict) -> dict:
    policies = config.get("policies", {})
    if not isinstance(policies, dict):
        raise ConfigError("policies must be an object.")

    max_notional_usd = _as_number(
        policies.get("max_notional_usd", 1_000),
        field="policies.max_notional_usd",
    )
    max_slippage_bps = _as_int(
        policies.get("max_slippage_bps", 200),
        field="policies.max_slippage_bps",
    )

    if max_notional_usd <= 0:
        raise ConfigError("policies.max_notional_usd must be > 0.")
    if max_slippage_bps < 1:
        raise ConfigError("policies.max_slippage_bps must be >= 1.")

    return {
        "max_notional_usd": max_notional_usd,
        "max_slippage_bps": max_slippage_bps,
    }


def _resolve_execution(config: dict) -> dict:
    execution = config.get("execution", {})
    if not isinstance(execution, dict):
        raise ConfigError("execution must be an object.")

    executor = execution.get("executor", {})
    if executor is None:
        executor = {}
    if not isinstance(executor, dict):
        raise ConfigError("execution.executor must be an object.")

    executor_type = str(executor.get("type", "manual")).strip().lower()
    if not executor_type:
        executor_type = "manual"

    return {
        "confirm_live_handoff": bool(execution.get("confirm_live_handoff", False)),
        "executor": {
            "name": str(executor.get("name", "")).strip(),
            "type": executor_type,
        },
    }


def _build_mcp_plan(inputs: dict) -> list[dict]:
    plan = [
        {
            "step": "scan_opportunities",
            "tool": "scan_opportunities",
            "args": {
                "chain": inputs["chain"],
                "underlying_symbol": inputs["underlying_symbol"],
                "capital_usd": inputs["capital_usd"],
                "top_n": inputs["top_n"],
                "min_liquidity_usd": inputs["min_liquidity_usd"],
                "max_price_impact_pct": inputs["max_price_impact_pct"],
                "ve_spectra_balance": inputs["ve_spectra_balance"],
                "compact": True,
            },
        },
        {
            "step": "select_candidate",
            "tool": "transform.select_top_pt_candidate",
            "args": {
                "underlying_symbol": inputs["underlying_symbol"],
                "target_maturity_days_min": inputs["target_maturity_days_min"],
                "target_maturity_days_max": inputs["target_maturity_days_max"],
                "pt_address_override": inputs["pt_address"],
            },
        },
        {
            "step": "quote_trade",
            "tool": "quote_trade",
            "args": {
                "chain": inputs["chain"],
                "side": inputs["side"],
                "capital_usd": inputs["capital_usd"],
                "pt_address": inputs["pt_address"] or "<selected_pt_address>",
            },
        },
        {
            "step": "simulate_portfolio_after_trade",
            "tool": "simulate_portfolio_after_trade",
            "args": {
                "chain": inputs["chain"],
                "side": inputs["side"],
                "capital_usd": inputs["capital_usd"],
                "pt_address": inputs["pt_address"] or "<selected_pt_address>",
                "wallet_address": inputs["wallet_address"] or "<required_for_personal_simulation>",
            },
        },
    ]

    if inputs["include_looping"]:
        plan.append(
            {
                "step": "looping_context",
                "tool": "get_looping_strategy",
                "args": {
                    "chain": inputs["chain"],
                    "pt_address": inputs["pt_address"] or "<selected_pt_address>",
                    "capital_usd": inputs["capital_usd"],
                },
            }
        )

    return plan


def _guard_policy(inputs: dict, policies: dict) -> list[dict]:
    violations = []
    if inputs["capital_usd"] > policies["max_notional_usd"]:
        violations.append(
            {
                "policy": "max_notional_usd",
                "message": "Requested notional exceeds configured cap.",
                "value": inputs["capital_usd"],
                "limit": policies["max_notional_usd"],
            }
        )

    requested_slippage_bps = int(round(inputs["max_price_impact_pct"] * 100))
    if requested_slippage_bps > policies["max_slippage_bps"]:
        violations.append(
            {
                "policy": "max_slippage_bps",
                "message": "Requested price impact exceeds configured slippage cap.",
                "value": requested_slippage_bps,
                "limit": policies["max_slippage_bps"],
            }
        )

    return violations


def run_once(config: dict) -> dict:
    dry_run = bool(config.get("dry_run", DEFAULT_DRY_RUN))
    inputs = _resolve_inputs(config)
    policies = _resolve_policies(config)
    execution = _resolve_execution(config)

    violations = _guard_policy(inputs, policies)
    if violations:
        first = violations[0]
        return {
            "status": "error",
            "skill": "spectra-pt-yield-trader",
            "error_code": "policy_violation",
            "policy": first["policy"],
            "message": first["message"],
            "violations": violations,
        }

    mcp_plan = _build_mcp_plan(inputs)

    live_requested = bool(inputs["live_mode"] and not dry_run)
    live_confirmed = bool(execution["confirm_live_handoff"])

    if live_requested and not live_confirmed:
        return {
            "status": "ok",
            "skill": "spectra-pt-yield-trader",
            "dry_run": True,
            "mode": "analysis-only",
            "blocked_action": "execution_handoff",
            "message": "Live handoff requested but execution.confirm_live_handoff is false.",
            "workflow_step_count": len(mcp_plan),
            "mcp_plan": mcp_plan,
        }

    execution_handoff = {
        "enabled": live_requested and live_confirmed,
        "executor": execution["executor"],
        "warning": "Spectra MCP is read-only. Use external signer/executor for transaction submission.",
    }

    return {
        "status": "ok",
        "skill": "spectra-pt-yield-trader",
        "dry_run": not execution_handoff["enabled"],
        "mode": "analysis-only",
        "workflow_step_count": len(mcp_plan),
        "mcp_plan": mcp_plan,
        "execution_handoff": execution_handoff,
        "connectors": {
            "mcp_spectra": "mcp-spectra",
            "seren_cron": "seren-cron",
        },
        "inputs": inputs,
    }



# ---------------------------------------------------------------------------
# SerenBucks balance helpers
# ---------------------------------------------------------------------------

from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _runtime_api_key() -> str:
    """Return the Seren API key from environment (Desktop or .env)."""
    for env_name in ("API_KEY", "SEREN_API_KEY"):
        token = (os.getenv(env_name) or "").strip()
        if token:
            return token
    return ""


def _check_serenbucks_balance(api_key: str) -> float:
    """Check SerenBucks balance. Returns balance in USD or 0.0 on error."""
    try:
        request = Request(
            "https://api.serendb.com/wallet/balance",
            headers={
                "User-Agent": "seren-spectra-pt-yield-trader/1.0",
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

def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
        result = run_once(config=config)
    except ConfigError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
