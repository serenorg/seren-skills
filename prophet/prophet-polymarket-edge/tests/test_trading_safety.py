"""Tests for prophet/prophet-polymarket-edge/scripts/trading_safety.py.

These tests lock in the contract that issue #454 demands BEFORE
trading can be re-enabled in this skill: a future PR that wires
`--yes-live` to a working execution path must satisfy every gate in
this module first. Loosening these assertions without simultaneously
landing the corresponding mitigations (signal calibration, risk
framework, CLOB execution path) is a P0 defect.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
TRADING_SAFETY_PATH = SCRIPTS_DIR / "trading_safety.py"
AGENT_PATH = SCRIPTS_DIR / "agent.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def ts():
    return _load_module("trading_safety", TRADING_SAFETY_PATH)


def _full_passing_config() -> dict:
    return {
        "backtest": {
            "events": 200,
            "results": {"return_pct": 0.05},
        },
        "risk": {
            "max_kelly_fraction": 0.05,
            "max_position_notional_usd": 1000.0,
            "min_mid_price": 0.30,
            "max_mid_price": 0.70,
            "min_daily_volume_usd": 5000.0,
            "min_seconds_to_resolution": 14 * 24 * 60 * 60,
            "max_inventory_hold_cycles": 3,
        },
    }


def _full_passing_env() -> dict:
    return {
        "POLY_PRIVATE_KEY": "0xabc",
        "POLY_API_KEY": "key",
        "POLY_PASSPHRASE": "pass",
        "POLY_SECRET": "secret",
    }


# ---------------------------------------------------------------------------
# Signal-calibration gate
# ---------------------------------------------------------------------------


def test_signal_calibration_gate_blocks_when_no_backtest(ts) -> None:
    result = ts.check_signal_calibration_gate(config={})
    assert result.passed is False
    assert result.error_code == "insufficient_sample_size"
    assert "backtest" in result.missing


def test_signal_calibration_gate_blocks_on_small_sample(ts) -> None:
    config = {"backtest": {"events": 50, "results": {"return_pct": 0.10}}}
    result = ts.check_signal_calibration_gate(config=config)
    assert result.passed is False
    assert result.error_code == "insufficient_sample_size"


def test_signal_calibration_gate_blocks_on_non_positive_return(ts) -> None:
    config = {"backtest": {"events": 500, "results": {"return_pct": -0.01}}}
    result = ts.check_signal_calibration_gate(config=config)
    assert result.passed is False
    assert result.error_code == "backtest_gate_blocked"


def test_signal_calibration_gate_blocks_on_zero_return(ts) -> None:
    config = {"backtest": {"events": 500, "results": {"return_pct": 0.0}}}
    result = ts.check_signal_calibration_gate(config=config)
    assert result.passed is False
    assert result.error_code == "backtest_gate_blocked"


def test_signal_calibration_gate_passes_with_valid_backtest(ts) -> None:
    config = {"backtest": {"events": 200, "results": {"return_pct": 0.04}}}
    result = ts.check_signal_calibration_gate(config=config)
    assert result.passed is True
    assert result.error_code is None


# ---------------------------------------------------------------------------
# Risk-framework gate
# ---------------------------------------------------------------------------


def test_risk_framework_gate_blocks_when_risk_block_missing(ts) -> None:
    result = ts.check_risk_framework_gate(config={})
    assert result.passed is False
    assert result.error_code == "risk_framework_missing"
    # All seven required fields should be flagged.
    assert set(result.missing) == set(ts.REQUIRED_RISK_FIELDS)


def test_risk_framework_gate_blocks_on_partial_fields(ts) -> None:
    config = {"risk": {"max_kelly_fraction": 0.05}}
    result = ts.check_risk_framework_gate(config=config)
    assert result.passed is False
    assert result.error_code == "risk_framework_missing"
    # max_kelly_fraction is provided, the other six are not.
    assert "max_kelly_fraction" not in result.missing
    assert "max_position_notional_usd" in result.missing


def test_risk_framework_gate_blocks_on_unsafe_kelly(ts) -> None:
    config = _full_passing_config()
    config["risk"]["max_kelly_fraction"] = 0.5  # > 0.10 hard cap
    result = ts.check_risk_framework_gate(config=config)
    assert result.passed is False
    assert result.error_code == "risk_framework_unsafe"
    assert "max_kelly_fraction" in result.missing


def test_risk_framework_gate_blocks_on_unsafe_midpoint_band(ts) -> None:
    config = _full_passing_config()
    config["risk"]["min_mid_price"] = 0.10  # below 0.30 floor
    result = ts.check_risk_framework_gate(config=config)
    assert result.passed is False
    assert result.error_code == "risk_framework_unsafe"


def test_risk_framework_gate_blocks_on_low_volume_floor(ts) -> None:
    config = _full_passing_config()
    config["risk"]["min_daily_volume_usd"] = 100.0  # below $5k floor
    result = ts.check_risk_framework_gate(config=config)
    assert result.passed is False
    assert result.error_code == "risk_framework_unsafe"


def test_risk_framework_gate_blocks_on_short_resolution_buffer(ts) -> None:
    config = _full_passing_config()
    config["risk"]["min_seconds_to_resolution"] = 60  # well below 14d
    result = ts.check_risk_framework_gate(config=config)
    assert result.passed is False
    assert result.error_code == "risk_framework_unsafe"


def test_risk_framework_gate_passes_with_valid_risk_block(ts) -> None:
    result = ts.check_risk_framework_gate(config=_full_passing_config())
    assert result.passed is True


# ---------------------------------------------------------------------------
# Execution-path gate
# ---------------------------------------------------------------------------


def test_execution_path_gate_blocks_when_clob_client_missing(ts, monkeypatch) -> None:
    """Force `import py_clob_client` to fail to simulate the v1 baseline."""
    real_import_module = ts.importlib.import_module

    def fake_import_module(name, *args, **kwargs):
        if name == "py_clob_client":
            raise ImportError("simulated: py_clob_client not installed")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(ts.importlib, "import_module", fake_import_module)
    result = ts.check_execution_path_gate(env=_full_passing_env())
    assert result.passed is False
    assert result.error_code == "clob_client_missing"
    assert "py_clob_client" in result.missing


def test_execution_path_gate_blocks_when_credentials_missing(ts, monkeypatch) -> None:
    """If py_clob_client IS importable, missing POLY_* env still trips the gate."""
    monkeypatch.setattr(
        ts.importlib,
        "import_module",
        lambda name, *a, **kw: object() if name == "py_clob_client" else ts.importlib.import_module(name, *a, **kw),
    )
    result = ts.check_execution_path_gate(env={})  # nothing set
    assert result.passed is False
    assert result.error_code == "poly_credentials_missing"
    # Both the private-key alternation and the three named keys should be missing.
    assert any("POLY_PRIVATE_KEY" in m for m in result.missing)
    assert "POLY_API_KEY" in result.missing
    assert "POLY_PASSPHRASE" in result.missing
    assert "POLY_SECRET" in result.missing


def test_execution_path_gate_accepts_wallet_private_key_alias(ts, monkeypatch) -> None:
    """WALLET_PRIVATE_KEY is an accepted alias for POLY_PRIVATE_KEY (matches
    polymarket/maker-rebate-bot/scripts/polymarket_live.py)."""
    monkeypatch.setattr(
        ts.importlib,
        "import_module",
        lambda name, *a, **kw: object() if name == "py_clob_client" else ts.importlib.import_module(name, *a, **kw),
    )
    env = {
        "WALLET_PRIVATE_KEY": "0xabc",
        "POLY_API_KEY": "k",
        "POLY_PASSPHRASE": "p",
        "POLY_SECRET": "s",
    }
    result = ts.check_execution_path_gate(env=env)
    assert result.passed is True


# ---------------------------------------------------------------------------
# Composite evaluator
# ---------------------------------------------------------------------------


def test_evaluate_returns_blocked_status_when_any_gate_fails(ts) -> None:
    payload = ts.evaluate_trading_safety_gates(config={}, env={})
    assert payload["status"] == "trading_safety_blocked"
    assert payload["passed"] is False
    blocker_gates = {b["gate"] for b in payload["blockers"]}
    # All three gates should fail at v1 baseline.
    assert blocker_gates == {"signal_calibration", "risk_framework", "execution_path"}


def test_evaluate_returns_ok_when_all_gates_pass(ts, monkeypatch) -> None:
    """Synthetic scenario: every gate passes. Asserts the contract is
    machine-checkable (i.e. a future trading-enable PR has a clear,
    concrete checklist to clear, and this is what 'cleared' looks like).
    """
    monkeypatch.setattr(
        ts.importlib,
        "import_module",
        lambda name, *a, **kw: object() if name == "py_clob_client" else ts.importlib.import_module(name, *a, **kw),
    )
    payload = ts.evaluate_trading_safety_gates(
        config=_full_passing_config(),
        env=_full_passing_env(),
    )
    assert payload["status"] == "ok"
    assert payload["passed"] is True
    assert payload["blockers"] == []
    assert {g["gate"] for g in payload["gates"]} == {
        "signal_calibration",
        "risk_framework",
        "execution_path",
    }


# ---------------------------------------------------------------------------
# agent.py wiring: --yes-live emits the blocked payload AND exits non-zero
# ---------------------------------------------------------------------------


def test_yes_live_emits_trading_safety_blocked_payload(capsys) -> None:
    """The CLI must surface the structured blocker checklist on stderr
    so a future trading-enable PR has the exact remediation list visible
    in CI output."""
    agent = _load_module("prophet_polymarket_edge_agent_for_yes_live", AGENT_PATH)
    rc = agent.main(["--yes-live"])
    assert rc == 2

    err_lines = capsys.readouterr().err.splitlines()
    # First line: the human-readable rejection reason.
    assert any("rejected at v1 launch" in line for line in err_lines)
    # Second line: the structured payload.
    json_lines = [line for line in err_lines if line.strip().startswith("{")]
    assert json_lines, "trading_safety payload must be emitted on stderr"
    payload = json.loads(json_lines[-1])
    assert payload["status"] == "trading_safety_blocked"
    assert payload["passed"] is False
    blocker_gates = {b["gate"] for b in payload["blockers"]}
    assert "signal_calibration" in blocker_gates
    assert "risk_framework" in blocker_gates
    # execution_path may or may not block depending on dev env, so don't pin it here.
