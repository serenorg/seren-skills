from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "kalshi" / "hybrid-signal-trader" / "scripts" / "agent.py"
SPEC = importlib.util.spec_from_file_location("kalshi_hybrid_signal_trader", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_live_mode_requires_yes_live_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEREN_API_KEY", "test-key")

    with pytest.raises(RuntimeError, match="--yes-live"):
        MODULE.ensure_runtime_ready(mode="live", yes_live=False)


def test_missing_seren_api_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEREN_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SEREN_API_KEY"):
        MODULE.ensure_runtime_ready(mode="scan", yes_live=False)


def test_scan_mode_returns_shared_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEREN_API_KEY", "test-key")
    MODULE.ensure_runtime_ready(mode="scan", yes_live=False)

    result = MODULE.build_result(mode="scan", dry_run=True)

    assert result["audit"]["contract_version"] == "kalshi-shared-v1"
    assert result["desktop_summary"]["style"] == "mini_research_note"
    assert result["selected_trades"]
    assert result["risk_note"]
