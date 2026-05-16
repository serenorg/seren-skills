"""Issue #591 — single_leg execution mode is removed.

The arb-bot's whole product identity is delta-neutral arbitrage. The
prior `single_leg` mode placed a Prophet limit without any Polymarket
hedge — that's directional speculation on Prophet's price discovery,
not arbitrage, and it has no place in this skill.

After #591:
  * `EXECUTION_MODE_DELTA_NEUTRAL` is the only valid value.
  * Configs that omit `execution_mode` get the default `delta_neutral`.
  * Configs that explicitly set `execution_mode: "single_leg"` are
    rejected with a clear deprecation error, NOT silently rewritten —
    the operator must remove the field so they read the new contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import AgentConfig


def _write_config(tmp_path: Path, overrides: dict | None = None) -> Path:
    """Lay down a minimal config.json with optional field overrides."""
    base = {
        "inputs": {"prophet_email": "x@example.com", "email_provider": "gmail", "manual_pairs": []},
        "storage": {"project_name": "prophet", "database_name": "prophet"},
        "scoring": {},
        "intelligence": {},
        "auto_discover": {},
        "live_mode": False,
        "max_orders_per_run": 5,
        "max_hedge_slippage_bps": 200.0,
    }
    if overrides:
        base.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(base))
    return path


def test_omitted_execution_mode_defaults_to_delta_neutral(tmp_path: Path) -> None:
    """A config that doesn't mention execution_mode should load with
    `delta_neutral` — the only valid value post-#591."""
    path = _write_config(tmp_path)

    config = AgentConfig.load(str(path))

    assert config.execution_mode == "delta_neutral"


def test_explicit_delta_neutral_loads_clean(tmp_path: Path) -> None:
    """The only valid explicit value must continue to load."""
    path = _write_config(tmp_path, {"execution_mode": "delta_neutral"})

    config = AgentConfig.load(str(path))

    assert config.execution_mode == "delta_neutral"


def test_single_leg_is_rejected_with_clear_deprecation_error(tmp_path: Path) -> None:
    """The footgun: a config that explicitly opts into single_leg must
    NOT load. The error must mention single_leg + delta_neutral so the
    operator knows what to change."""
    path = _write_config(tmp_path, {"execution_mode": "single_leg"})

    with pytest.raises(ValueError) as exc_info:
        AgentConfig.load(str(path))

    msg = str(exc_info.value).lower()
    assert "single_leg" in msg
    assert "delta_neutral" in msg


def test_unknown_execution_mode_is_also_rejected(tmp_path: Path) -> None:
    """Don't open the door for typos like `deltaneutral` or future
    legacy variants. Anything other than delta_neutral fails."""
    path = _write_config(tmp_path, {"execution_mode": "gibberish"})

    with pytest.raises(ValueError):
        AgentConfig.load(str(path))
