from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures"
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _load_agent_module():
    spec = importlib.util.spec_from_file_location(
        "euler_base_vault_bot_agent_test",
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
    assert payload["skill"] == "euler-base-vault-bot"


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


def test_live_mode_requires_allow_live_flag() -> None:
    result = module.run_once(
        config={
            "connectors": ["rpc_base"],
            "inputs": {"action": "status", "wallet_mode": "local", "live_mode": True},
        },
        dry_run=False,
        allow_live=False,
    )

    assert result["error_code"] == "live_confirmation_required"
    assert "--allow-live" in result["message"]


def test_live_dependencies_fail_closed_without_wallet_private_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEREN_API_KEY", "sb_test")
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="WALLET_PRIVATE_KEY is required"):
        module._validate_runtime_dependencies(
            {
                "connectors": ["rpc_base"],
                "inputs": {"action": "deposit", "wallet_mode": "local"},
            },
            live_requested=True,
        )


def test_emergency_exit_stops_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module,
        "_validate_runtime_dependencies",
        lambda config, live_requested: {
            "connectors": ["rpc_base"],
            "runtime_api_key_present": True,
            "wallet_mode": "local",
        },
    )

    result = module.run_emergency_exit(
        {"connectors": ["rpc_base"], "inputs": {"action": "withdraw", "wallet_mode": "local"}}
    )

    assert result["stop_trading"] is True
    assert result["liquidate_position"] is True
    assert "stop trading" in result["message"]
