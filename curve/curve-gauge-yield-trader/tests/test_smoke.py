from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures"
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _load_agent_module():
    spec = importlib.util.spec_from_file_location(
        "curve_gauge_yield_trader_agent_test",
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
    assert payload["skill"] == "curve-gauge-yield-trader"


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
    assert payload["blocked_action"] == "live_execution"


def test_unwind_all_requires_seren_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEREN_API_KEY", raising=False)

    with pytest.raises(module.ConfigError, match="SEREN_API_KEY is required"):
        module.run_unwind_all(config={}, ledger_address="")


def test_unwind_all_stops_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEREN_API_KEY", "sb_test")
    monkeypatch.setattr(module, "SerenPublisherClient", lambda api_key, base_url: {"api_key": api_key, "base_url": base_url})
    monkeypatch.setattr(
        module,
        "_resolve_inputs",
        lambda config: {
            "wallet_mode": "ledger",
            "chain": "base",
            "top_n_gauges": 3,
            "deposit_token": "USDC",
            "deposit_amount_usd": 100.0,
        },
    )
    monkeypatch.setattr(module, "resolve_signer", lambda wallet_mode, wallet_path, ledger_address: {"mode": wallet_mode, "address": ledger_address})
    monkeypatch.setattr(
        module,
        "check_rpc_capability",
        lambda client, chain, config: {
            "publisher": "base-rpc",
            "rpc_target": {"method": "POST", "path": "/"},
            "publisher_source": "catalog",
        },
    )
    monkeypatch.setattr(module, "fetch_top_gauges", lambda client, chain, limit: {"rows": [{"gauge": "0x1"}]})
    monkeypatch.setattr(module, "choose_trade_plan", lambda gauges_response, token, amount_usd: {"gauge_address": "0x1", "amount_usd": amount_usd})
    monkeypatch.setattr(module, "sync_positions", lambda client, signer, rpc_target, trade_plan: {"gauge_address": trade_plan["gauge_address"]})

    result = module.run_unwind_all(config={"wallet": {"ledger_address": "0xabc"}}, ledger_address="")

    assert result["stop_trading"] is True
    assert result["liquidate_position"] is True
    assert "stop trading" in result["message"]


def test_live_mode_requires_yes_live_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEREN_API_KEY", "sb_test")
    monkeypatch.setattr(module, "SerenPublisherClient", lambda api_key, base_url: {"api_key": api_key, "base_url": base_url})
    monkeypatch.setattr(
        module,
        "_resolve_inputs",
        lambda config: {
            "live_mode": True,
            "wallet_mode": "ledger",
            "chain": "base",
            "top_n_gauges": 3,
            "deposit_token": "USDC",
            "deposit_amount_usd": 100.0,
        },
    )
    monkeypatch.setattr(module, "resolve_signer", lambda wallet_mode, wallet_path, ledger_address: {"mode": wallet_mode, "address": ledger_address or "0xabc"})
    monkeypatch.setattr(module, "_resolve_evm_execution", lambda config: {"strategy": "gauge_stake_lp"})
    monkeypatch.setattr(
        module,
        "check_rpc_capability",
        lambda client, chain, config: {
            "publisher": "base-rpc",
            "rpc_target": {"method": "POST", "path": "/"},
            "publisher_source": "catalog",
        },
    )
    monkeypatch.setattr(module, "fetch_top_gauges", lambda client, chain, limit: {"rows": [{"gauge": "0x1"}]})
    monkeypatch.setattr(module, "choose_trade_plan", lambda gauges_response, token, amount_usd: {"gauge_address": "0x1", "amount_usd": amount_usd})
    monkeypatch.setattr(module, "sync_positions", lambda client, signer, rpc_target, trade_plan: {"gauge_address": trade_plan["gauge_address"]})
    monkeypatch.setattr(module, "preflight_liquidity", lambda *args, **kwargs: {"transactions": [{"unsigned_tx": "0xabc"}]})

    with pytest.raises(module.ConfigError, match="--yes-live was not provided"):
        module.run_once(config={"dry_run": False, "wallet": {"ledger_address": "0xabc"}}, yes_live=False, ledger_address="")
