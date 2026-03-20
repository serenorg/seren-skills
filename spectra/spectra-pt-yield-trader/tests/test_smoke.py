from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


FIXTURE_DIR = Path(__file__).parent / "fixtures"
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _load_agent_module():
    spec = importlib.util.spec_from_file_location(
        "spectra_pt_yield_trader_agent_test",
        SCRIPT_DIR / "agent.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


module = _load_agent_module()


def test_happy_path_fixture_is_successful() -> None:
    payload = _read_fixture("happy_path.json")
    assert payload["status"] == "ok"
    assert payload["skill"] == "spectra-pt-yield-trader"


def test_connector_failure_fixture_has_error_code() -> None:
    payload = _read_fixture("connector_failure.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "connector_failure"


def test_policy_violation_fixture_has_error_code() -> None:
    payload = _read_fixture("policy_violation.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "policy_violation"


def test_dry_run_fixture_blocks_live_execution() -> None:
    payload = _read_fixture("dry_run_guard.json")
    assert payload["dry_run"] is True
    assert payload["blocked_action"] == "execution_handoff"


def test_live_handoff_requires_yes_live_flag() -> None:
    result = module.run_once(
        config={
            "dry_run": False,
            "inputs": {
                "chain": "base",
                "wallet_mode": "delegated",
                "side": "buy",
                "capital_usd": 100,
                "top_n": 3,
                "min_liquidity_usd": 1_000,
                "max_price_impact_pct": 1.0,
                "target_maturity_days_min": 7,
                "target_maturity_days_max": 30,
                "pt_address": "0xpt",
                "wallet_address": "0xwallet",
                "live_mode": True,
            },
            "policies": {"max_notional_usd": 500, "max_slippage_bps": 200},
            "execution": {"confirm_live_handoff": True, "executor": {"type": "manual"}},
        },
        yes_live=False,
    )

    assert result["blocked_action"] == "execution_handoff"
    assert "--yes-live" in result["message"]


def test_stop_trading_emits_sell_side_unwind_handoff() -> None:
    result = module.run_stop_trading(
        config={
            "inputs": {
                "chain": "base",
                "wallet_mode": "delegated",
                "side": "buy",
                "capital_usd": 100,
                "top_n": 3,
                "min_liquidity_usd": 1_000,
                "max_price_impact_pct": 1.0,
                "target_maturity_days_min": 7,
                "target_maturity_days_max": 30,
                "pt_address": "0xpt",
                "wallet_address": "0xwallet",
            },
            "execution": {"executor": {"type": "manual"}},
        }
    )

    assert result["stop_trading"] is True
    assert result["liquidate_position"] is True
    assert result["execution_handoff"]["side"] == "sell"
    assert "stop trading" in result["message"]
