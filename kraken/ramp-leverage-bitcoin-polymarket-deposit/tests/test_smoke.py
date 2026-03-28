"""Critical smoke tests for kraken/ramp-leverage-bitcoin-polymarket-deposit."""

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


# ── Fixture validation ───────────────────────────────────────────────

def test_happy_path_fixture() -> None:
    payload = _read_fixture("happy_path.json")
    assert payload["status"] == "ok"
    assert payload["skill"] == "ramp-leverage-bitcoin-polymarket-deposit"


def test_connector_failure_fixture() -> None:
    payload = _read_fixture("connector_failure.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "connector_failure"


def test_dry_run_fixture() -> None:
    payload = _read_fixture("dry_run_guard.json")
    assert payload["dry_run"] is True
    assert payload["blocked_action"] == "live_execution"


# ── Agent unit tests ─────────────────────────────────────────────────

def test_defaults_to_dry_run() -> None:
    agent = _load_agent()
    assert agent.DEFAULT_DRY_RUN is True


def test_default_leverage_is_5x() -> None:
    agent = _load_agent()
    assert agent.DEFAULT_LEVERAGE == 5


def test_rejects_missing_api_keys() -> None:
    agent = _load_agent()
    env = {"POLYMARKET_WALLET_ADDRESS": "0x1234"}
    with mock.patch.dict(os.environ, env, clear=True):
        try:
            agent.run_pipeline({}, dry_run=True)
            assert False, "Should have exited"
        except SystemExit:
            pass


def test_kraken_client_sign_produces_headers() -> None:
    agent = _load_agent()
    # Use a dummy base64-encoded secret (32 bytes)
    import base64
    dummy_secret = base64.b64encode(b"0" * 32).decode()
    client = agent.KrakenClient("test-key", dummy_secret)
    headers = client._sign("/0/private/Balance", {"nonce": "1234567890"})
    assert "API-Key" in headers
    assert headers["API-Key"] == "test-key"
    assert "API-Sign" in headers
    assert len(headers["API-Sign"]) > 0


def test_config_bootstrap() -> None:
    agent = _load_agent()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        example = Path(tmp) / "config.example.json"
        example.write_text('{"dry_run": true, "inputs": {"leverage": 5}}')
        result = agent._bootstrap_config_path(str(Path(tmp) / "config.json"))
        assert result.exists()
        data = json.loads(result.read_text())
        assert data["inputs"]["leverage"] == 5
