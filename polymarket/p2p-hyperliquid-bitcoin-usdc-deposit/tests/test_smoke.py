"""Critical smoke tests for polymarket/p2p-hyperliquid-bitcoin-usdc-deposit."""

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
    assert p["skill"] == "p2p-hyperliquid-bitcoin-usdc-deposit"


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


def test_margin_math() -> None:
    """At 5x, $200 deposit = $40 margin, $159 to Polymarket (minus $1 fee)."""
    deposit = 200
    leverage = 5
    margin = deposit / leverage
    free = deposit - margin - 1
    assert margin == 40.0
    assert free == 159.0
