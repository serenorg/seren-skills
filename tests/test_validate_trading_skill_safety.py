from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "validate_trading_skill_safety.py"
)
SPEC = importlib.util.spec_from_file_location("validate_trading_skill_safety", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_detect_trading_requires_execution_signal(tmp_path) -> None:
    skill_dir = tmp_path / "seren" / "job-helper"
    _write(
        skill_dir / "SKILL.md",
        """---
name: job-helper
description: Help candidates prepare.
---

# Job Helper

This skill discusses tradeoffs in interviews and resumes.
""",
    )
    context = MODULE.build_context(skill_dir, [], {})
    is_trading, reasons = MODULE.detect_trading(context, {})

    assert is_trading is False
    assert reasons == []


def test_trading_skill_missing_guardrails_fails(tmp_path) -> None:
    skill_dir = tmp_path / "kraken" / "grid-trader"
    _write(
        skill_dir / "SKILL.md",
        """---
name: grid-trader
description: Trade BTC automatically with a grid bot.
---

# Grid Trader

Grid trading bot with live mode.
""",
    )
    _write(
        skill_dir / "config.example.json",
        json.dumps({"dry_run": True, "execution": {"live_mode": False}}, indent=2),
    )
    _write(
        skill_dir / "scripts" / "agent.py",
        """import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--allow-live", action="store_true")

def main():
    raise RuntimeError("SEREN_API_KEY is required")
""",
    )

    context = MODULE.build_context(
        skill_dir,
        [Path("kraken/grid-trader/scripts/agent.py")],
        {},
    )
    result = MODULE.validate_context(context, {}, {}, enforce_tests=True)
    rules = {item.rule for item in result.violations}

    assert result.is_trading is True
    assert "skill_trade_execution_contract" in rules
    assert "skill_pre_trade_checklist" in rules
    assert "skill_dependency_validation" in rules
    assert "tests_guardrail_coverage" in rules


def test_orderbook_skill_with_waiver_and_shared_tests_passes(tmp_path) -> None:
    skill_dir = tmp_path / "polymarket" / "maker-rebate-bot"
    _write(
        skill_dir / "SKILL.md",
        """---
name: maker-rebate-bot
description: Provide two-sided liquidity on a prediction market.
---

# Maker Rebate Bot

## Trade Execution Contract

When the user says sell, close, exit, unwind, or flatten, execute immediately and ask only the minimum clarifying question if the position target is ambiguous.

## CLOB Exit Rules

- Use tick_size from the live order book.
- Use marketable exits at the best bid.
- Never place a passive sell for an immediate exit.
- Estimate recovery across all levels of visible bid depth.

## Pre-Trade Checklist

1. Fetch the live order book.
2. Snap prices to tick_size.
3. Verify py-clob-client and POLY_API_KEY are loaded.
4. Fail closed with a remediation message if anything is missing.

## Execution Modes

Live mode requires both execution.live_mode=true and --yes-live.

## Emergency Exit

Run `python scripts/agent.py --unwind-all --yes-live` to cancel all orders and liquidate inventory.
""",
    )
    _write(skill_dir / "requirements.txt", "py-clob-client>=0.34.6\n")
    _write(
        skill_dir / "config.example.json",
        json.dumps({"execution": {"live_mode": False}, "dry_run": True}, indent=2),
    )
    _write(
        skill_dir / "scripts" / "agent.py",
        """import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--yes-live", action="store_true")
parser.add_argument("--unwind-all", action="store_true")

def cancel_all_orders():
    return "cancel all orders"

def liquidate_inventory():
    return "liquidate inventory"

def marketable_exit():
    tick_size = 0.01
    best_bid = 0.35
    estimated_exit_value = 4.2
    return tick_size, best_bid, estimated_exit_value

def main():
    args = parser.parse_args()
    if args.unwind_all:
        cancel_all_orders()
        liquidate_inventory()
        return
    raise RuntimeError("Missing required POLY_API_KEY")
""",
    )
    repo_tests = {
        tmp_path / "polymarket" / "tests" / "test_execution_safety.py": """
def test_unwind_all_requires_yes_live_confirmation():
    assert 'live_confirmation_required'

def test_missing_poly_api_key_fails_closed():
    assert 'RuntimeError'

def test_unwind_all_cancels_orders_and_liquidates_inventory():
    assert 'cancel_all'

def test_marketable_sell_plan_uses_min_tick_and_full_bid_sweep():
    tick_size = 0.01
    best_bid = 0.35
    estimated_exit_value = 4.2
"""
    }

    context = MODULE.build_context(
        skill_dir,
        [Path("polymarket/maker-rebate-bot/scripts/agent.py")],
        repo_tests,
    )
    result = MODULE.validate_context(
        context,
        {},
        {"polymarket/maker-rebate-bot": {"rules": ["runtime_scheduler_safety"], "reason": "scheduler files are not part of this fixture"}},
        enforce_tests=True,
    )

    assert result.is_trading is True
    assert result.violations == []
    assert "runtime_scheduler_safety" in result.waived_rules or not context.has_scheduler
