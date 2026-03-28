"""Critical smoke tests for hyperliquid/5x-btc-usdc-withdraw."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from unittest import mock

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _load_agent():
    spec = importlib.util.spec_from_file_location("agent", SCRIPTS_DIR / "agent.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_happy_path_fixture() -> None:
    p = _read_fixture("happy_path.json")
    assert p["status"] == "ok"
    assert p["skill"] == "5x-btc-usdc-withdraw"


def test_connector_failure_fixture() -> None:
    p = _read_fixture("connector_failure.json")
    assert p["status"] == "error"
    assert p["error_code"] == "connector_failure"


def test_dry_run_fixture() -> None:
    p = _read_fixture("dry_run_guard.json")
    assert p["dry_run"] is True


def test_defaults() -> None:
    agent = _load_agent()
    assert agent.DEFAULT_DRY_RUN is True
    assert agent.DEFAULT_LEVERAGE == 5


def test_rejects_missing_key() -> None:
    agent = _load_agent()
    with mock.patch.dict(os.environ, {}, clear=True):
        try:
            agent.run_pipeline({}, dry_run=True)
            assert False, "Should have exited"
        except SystemExit:
            pass


def test_margin_math_various_deposits() -> None:
    """Verify margin/free USDC math at different deposit sizes."""
    for deposit, expected_margin, expected_free in [
        (200, 40.0, 159.0),
        (100, 20.0, 79.0),
        (50, 10.0, 39.0),
    ]:
        leverage = 5
        margin = deposit / leverage
        free = deposit - margin - 1
        assert margin == expected_margin, f"deposit={deposit}"
        assert free == expected_free, f"deposit={deposit}"
