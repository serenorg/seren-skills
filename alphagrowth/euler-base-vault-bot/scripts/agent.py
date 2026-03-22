#!/usr/bin/env python3
"""Generated SkillForge runtime for euler-base-vault-bot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import os
import sys
from urllib.request import Request, urlopen

from normalized_trade_store import NormalizedTradingStore


DEFAULT_DRY_RUN = True
AVAILABLE_CONNECTORS = ['rpc_base']


class ConfigError(RuntimeError):
    """Raised when runtime config or dependencies are invalid."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run generated SkillForge agent runtime.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to runtime config file (default: config.json).",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Explicit startup-only opt-in for live execution.",
    )
    parser.add_argument(
        "--emergency-exit",
        action="store_true",
        help="Stop trading and liquidate the tracked vault position.",
    )
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


def load_config(config_path: str) -> dict:
    path = _bootstrap_config_path(config_path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_runtime_dependencies(config: dict, *, live_requested: bool) -> dict:
    connectors = config.get("connectors", [])
    if not isinstance(connectors, list):
        raise ConfigError("connectors must be a list.")
    if "rpc_base" not in connectors:
        raise ValueError("Unsupported connector set: rpc_base is required.")

    inputs = config.get("inputs", {})
    if not isinstance(inputs, dict):
        raise ConfigError("inputs must be an object.")

    action = str(inputs.get("action", "status")).strip().lower()
    if action not in {"status", "deposit", "compound", "withdraw"}:
        raise ValueError(f"Unsupported action '{action}'.")

    wallet_mode = str(inputs.get("wallet_mode", "local")).strip().lower()
    if wallet_mode not in {"local", "ledger"}:
        raise ValueError(f"Unsupported wallet_mode '{wallet_mode}'.")

    runtime_api_key = _runtime_api_key()
    if live_requested and not runtime_api_key:
        raise RuntimeError("SEREN_API_KEY is required for rpc_base live execution.")

    if wallet_mode == "local":
        has_local_wallet = bool((os.getenv("WALLET_PRIVATE_KEY") or "").strip())
        if live_requested and not has_local_wallet:
            raise RuntimeError("WALLET_PRIVATE_KEY is required for local live execution.")
    else:
        has_ledger_address = bool((os.getenv("LEDGER_ADDRESS") or "").strip())
        if live_requested and not has_ledger_address:
            raise RuntimeError("LEDGER_ADDRESS is required for ledger live execution.")

    return {
        "connectors": connectors,
        "runtime_api_key_present": bool(runtime_api_key),
        "wallet_mode": wallet_mode,
    }


def run_once(config: dict, dry_run: bool, *, allow_live: bool) -> dict:
    live_requested = bool(config.get("inputs", {}).get("live_mode", False) and not dry_run)
    if live_requested and not allow_live:
        return {
            "status": "error",
            "skill": "euler-base-vault-bot",
            "error_code": "live_confirmation_required",
            "message": "Live mode requested but --allow-live was not provided.",
            "dry_run": True,
        }

    dependencies = _validate_runtime_dependencies(config, live_requested=live_requested)
    return {
        "status": "ok",
        "dry_run": dry_run,
        "live_requested": live_requested,
        "connectors": AVAILABLE_CONNECTORS,
        "input_keys": sorted(config.get("inputs", {}).keys()),
        "dependencies": dependencies,
    }


def run_emergency_exit(config: dict) -> dict:
    dependencies = _validate_runtime_dependencies(config, live_requested=False)
    return {
        "status": "ok",
        "skill": "euler-base-vault-bot",
        "mode": "emergency_exit",
        "stop_trading": True,
        "cancel_all_orders": "not_applicable_for_vault_positions",
        "liquidate_position": True,
        "dependencies": dependencies,
        "message": (
            "stop trading and liquidate position via the full vault withdrawal workflow "
            "before restarting automation."
        ),
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
                "User-Agent": "seren-euler-base-vault-bot/1.0",
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


def _persist_normalized_result(config: dict, result: dict, *, run_type: str) -> None:
    store = NormalizedTradingStore(
        os.getenv("SERENDB_URL"),
        skill_slug="euler-base-vault-bot",
        venue="euler",
        strategy_name="euler-base-vault-bot",
    )
    inputs = config.get("inputs", {}) if isinstance(config.get("inputs"), dict) else {}
    action = str(inputs.get("action", "status"))
    order_events = [
        {
            "order_id": action,
            "instrument_id": str(inputs.get("vault_address") or inputs.get("asset") or "euler-vault"),
            "symbol": str(inputs.get("asset") or inputs.get("underlying_symbol") or "EULER-VAULT"),
            "side": "EXIT" if run_type == "emergency_exit" else action.upper(),
            "order_type": "vault_action",
            "event_type": "vault_workflow",
            "status": result.get("status", "ok"),
            "notional_usd": inputs.get("capital_usd"),
            "metadata": {"dependencies": result.get("dependencies", {})},
        }
    ]
    try:
        store.persist_completed_run(
            mode=str(result.get("mode") or action),
            dry_run=bool(result.get("dry_run", True)),
            config=config,
            status=str(result.get("status", "ok")),
            summary={
                "live_requested": result.get("live_requested"),
                "connectors": result.get("connectors", []),
            },
            order_events=order_events,
            metadata={"run_type": run_type},
            error_code=result.get("error_code"),
            error_message=result.get("message"),
        )
    finally:
        store.close()

def main() -> int:
    args = parse_args()
    try:
        config = load_config(args.config)
        if args.emergency_exit:
            result = run_emergency_exit(config=config)
            _persist_normalized_result(config, result, run_type="emergency_exit")
        else:
            dry_run = bool(config.get("dry_run", DEFAULT_DRY_RUN))
            result = run_once(config=config, dry_run=dry_run, allow_live=bool(args.allow_live))
            _persist_normalized_result(config, result, run_type="run_once")
    except (ConfigError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
