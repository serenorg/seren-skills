"""Critical smoke tests for p2p-leverage-bitcoin-usdc-deposit."""

from __future__ import annotations

import json
import importlib.util
import os
import sys
from pathlib import Path
from unittest import mock

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _load_agent():
    """Import agent.py without running main."""
    spec = importlib.util.spec_from_file_location("agent", SCRIPTS_DIR / "agent.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Fixture validation ───────────────────────────────────────────────

def test_happy_path_fixture_is_successful() -> None:
    payload = _read_fixture("happy_path.json")
    assert payload["status"] == "ok"
    assert payload["skill"] == "p2p-leverage-bitcoin-usdc-deposit"


def test_connector_failure_fixture_has_error_code() -> None:
    payload = _read_fixture("connector_failure.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "connector_failure"


def test_dry_run_fixture_blocks_live_execution() -> None:
    payload = _read_fixture("dry_run_guard.json")
    assert payload["dry_run"] is True
    assert payload["blocked_action"] == "live_execution"


# ── Agent unit tests ─────────────────────────────────────────────────

def test_agent_defaults_to_dry_run() -> None:
    agent = _load_agent()
    assert agent.DEFAULT_DRY_RUN is True


def test_agent_rejects_missing_private_key() -> None:
    agent = _load_agent()
    env = {"SEREN_API_KEY": "test-key"}
    with mock.patch.dict(os.environ, env, clear=True):
        try:
            agent.step_setup({}, "https://api.serendb.com", "test-key")
            assert False, "Should have exited"
        except SystemExit:
            pass  # Expected: _fatal calls sys.exit


def test_config_bootstrap_copies_example() -> None:
    agent = _load_agent()
    import tempfile
    import shutil

    with tempfile.TemporaryDirectory() as tmp:
        example = Path(tmp) / "config.example.json"
        example.write_text('{"dry_run": true}')
        result = agent._bootstrap_config_path(str(Path(tmp) / "config.json"))
        assert result.exists()
        data = json.loads(result.read_text())
        assert data["dry_run"] is True


def test_pad32_encodes_address() -> None:
    agent = _load_agent()
    result = agent._pad32("0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf")
    assert len(result) == 64
    assert result.startswith("000000000000000000000000")
    assert "cbb7c0000ab88b473b1f5afd9ef808440eed33bf" in result


def test_decode_uint256_at_offset() -> None:
    agent = _load_agent()
    # Two slots: slot 0 = 42, slot 1 = 100
    hex_data = "000000000000000000000000000000000000000000000000000000000000002a" \
               "0000000000000000000000000000000000000000000000000000000000000064"
    assert agent._decode_uint256(hex_data, 0) == 42
    assert agent._decode_uint256(hex_data, 1) == 100
