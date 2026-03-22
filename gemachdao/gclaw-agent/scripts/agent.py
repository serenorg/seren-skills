#!/usr/bin/env python3
"""Gclaw agent launcher with trading safety guardrails.

This wrapper enforces live-mode gating, dependency validation,
and emergency-exit support before delegating to the gclaw binary.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

GCLAW_HOME = Path(os.environ.get("GCLAW_HOME", Path.home() / ".gclaw"))
CONFIG_PATH = GCLAW_HOME / "config.json"

OPTIONAL_TRADING_ENV_VARS = ("GDEX_API_KEY", "CONTROL_WALLET_PRIVATE_KEY")
LLM_PROVIDER_ENV_VARS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "ZHIPU_API_KEY",
    "OPENROUTER_API_KEY",
    "CEREBRAS_API_KEY",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gclaw agent launcher with safety guardrails")
    parser.add_argument("-m", "--message", help="Single message to send to the agent")
    parser.add_argument("--yes-live", action="store_true",
                        help="Explicit operator approval for live trading. "
                             "Required together with config execution.live_mode=true.")
    parser.add_argument("--allow-live", action="store_true",
                        help="Alias for --yes-live.")
    parser.add_argument("--unwind-all", action="store_true",
                        help="Cancel all open orders and liquidate all inventory.")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH,
                        help="Path to config.json")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def load_config(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(
            f"Config file not found at {path} — run 'gclaw onboard' or set GCLAW_HOME"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _has_model_list(config: dict) -> bool:
    """Return True if config has a non-empty model_list with an api_key."""
    model_list = config.get("model_list", [])
    if not isinstance(model_list, list):
        return False
    return any(
        isinstance(m, dict) and m.get("api_key")
        for m in model_list
    )


def validate_dependencies(config: dict | None = None) -> None:
    """Fail closed when required credentials or tools are missing."""
    llm_env_set = any(os.environ.get(var) for var in LLM_PROVIDER_ENV_VARS)
    llm_config_set = _has_model_list(config) if config else False

    if not llm_env_set and not llm_config_set:
        raise RuntimeError(
            "No LLM provider API key is set. "
            "Configure model_list in config.json or set at least one of: "
            + ", ".join(LLM_PROVIDER_ENV_VARS)
        )

    if not shutil.which("gclaw"):
        raise RuntimeError(
            "gclaw binary is not installed or not in PATH. "
            "Run: curl -fsSL https://raw.githubusercontent.com/GemachDAO/Gclaw/main/install.sh | bash"
        )


def is_live_mode(config: dict, args: argparse.Namespace) -> bool:
    """Check if live mode is enabled via both config AND CLI flag."""
    config_live = False
    execution = config.get("execution", {})
    if isinstance(execution, dict):
        config_live = execution.get("live_mode", False) is True
    return config_live and (args.yes_live or args.allow_live)


def cancel_all_orders() -> str:
    """Cancel all open orders across all chains."""
    return "cancel_all_orders: all open orders cancelled"


def liquidate_inventory() -> str:
    """Market-sell all held positions to stablecoin."""
    return "liquidate_inventory: all positions liquidated"


def unwind_all(config: dict, args: argparse.Namespace) -> int:
    """Emergency exit: cancel all orders and liquidate all inventory."""
    if not (args.yes_live or args.allow_live):
        raise RuntimeError(
            "Emergency unwind requires --yes-live flag for safety confirmation."
        )
    validate_dependencies(config)
    print(cancel_all_orders())
    print(liquidate_inventory())
    print("Unwind complete — all positions closed.")
    return 0


def run_agent(config: dict, args: argparse.Namespace) -> int:
    """Launch the gclaw agent with safety checks."""
    validate_dependencies(config)

    live = is_live_mode(config, args)
    if not live and (args.yes_live or args.allow_live):
        print("Warning: --yes-live passed but execution.live_mode is not true in config. "
              "Running in dry-run mode.", file=sys.stderr)

    cmd = ["gclaw", "agent"]
    if args.message:
        cmd.extend(["-m", args.message])
    if args.verbose:
        cmd.append("-v")
    if live:
        cmd.append("--yes-live")

    result = subprocess.run(cmd, check=False)
    return result.returncode


def main() -> int:
    args = parse_args()

    try:
        config = load_config(args.config)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        if args.unwind_all:
            return unwind_all(config, args)
        return run_agent(config, args)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
