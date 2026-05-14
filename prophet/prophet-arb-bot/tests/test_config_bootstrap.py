"""Config auto-bootstrap (#542 Fix 1).

When `agent.py --command setup` runs against a fresh skill directory the
operator should not have to `cp config.example.json config.json` or edit
fields by hand. The bootstrap helper:

  1. Copies `config.example.json` to the target path if absent.
  2. Forces `auto_discover.enabled=true` and `live_mode=false` on new
     configs (safe defaults for first-time operators).
  3. Persists `--prophet-email` / `--email-provider` into
     `inputs.prophet_email` / `inputs.email_provider`.
  4. Is idempotent on existing configs — never overwrites.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config_bootstrap import (
    bootstrap_config_if_missing,
    BootstrapResult,
)


_EXAMPLE_CONTENT = {
    "inputs": {
        "prophet_email": "you@example.com",
        "email_provider": "gmail",
        "manual_pairs": [],
    },
    "storage": {"project_name": "prophet", "database_name": "prophet"},
    "scoring": {
        "min_spread": 0.03,
        "max_spread": 0.30,
        "kelly_fraction": 0.25,
        "max_trade_size_usdc": 50.0,
        "min_trade_size_usdc": 5.0,
        "bankroll_usdc": 200.0,
    },
    "intelligence": {
        "enabled": False,
        "max_basis_volatility": 0.05,
        "fetch_correlations": True,
    },
    "auto_discover": {
        "enabled": False,
        "min_24h_volume_usd": 10000.0,
        "min_headroom_hours": 24.0,
        "resolution_deadline_iso": "2026-05-24T23:59:59Z",
        "max_candidates": 50,
        "initial_bet_usdc": 1.0,
    },
    "live_mode": False,
    "max_orders_per_run": 5,
    "execution_mode": "single_leg",
    "max_hedge_slippage_bps": 200.0,
}


@pytest.fixture
def skill_root(tmp_path: Path) -> Path:
    """Lay down config.example.json + an empty scripts dir."""
    (tmp_path / "config.example.json").write_text(json.dumps(_EXAMPLE_CONTENT))
    return tmp_path


def test_bootstrap_creates_config_from_example_when_missing(skill_root: Path) -> None:
    target = skill_root / "config.json"
    assert not target.exists()

    result = bootstrap_config_if_missing(
        config_path=str(target),
        example_path=str(skill_root / "config.example.json"),
        prophet_email=None,
        email_provider=None,
    )

    assert result.created is True
    assert target.exists()
    data = json.loads(target.read_text())
    # Defaults flipped on for first-time operators:
    assert data["auto_discover"]["enabled"] is True
    assert data["live_mode"] is False


def test_bootstrap_persists_email_flags(skill_root: Path) -> None:
    target = skill_root / "config.json"

    result = bootstrap_config_if_missing(
        config_path=str(target),
        example_path=str(skill_root / "config.example.json"),
        prophet_email="jill@volume.finance",
        email_provider="outlook",
    )

    assert result.created is True
    data = json.loads(target.read_text())
    assert data["inputs"]["prophet_email"] == "jill@volume.finance"
    assert data["inputs"]["email_provider"] == "outlook"


def test_bootstrap_is_idempotent_when_config_exists(skill_root: Path) -> None:
    target = skill_root / "config.json"
    # Pre-existing config the operator has hand-tuned. Bootstrap must NOT
    # overwrite — even if --prophet-email is passed.
    target.write_text(json.dumps({"inputs": {"prophet_email": "preserved@x.com"}, "live_mode": True}))

    result = bootstrap_config_if_missing(
        config_path=str(target),
        example_path=str(skill_root / "config.example.json"),
        prophet_email="will-not-apply@x.com",
        email_provider="gmail",
    )

    assert result.created is False
    data = json.loads(target.read_text())
    # User's existing values preserved verbatim:
    assert data["inputs"]["prophet_email"] == "preserved@x.com"
    assert data["live_mode"] is True


def test_bootstrap_raises_when_example_also_missing(tmp_path: Path) -> None:
    """If neither config.json nor config.example.json exists, the skill
    is incorrectly installed — fail with a clear error rather than
    fabricating an empty config."""
    with pytest.raises(FileNotFoundError):
        bootstrap_config_if_missing(
            config_path=str(tmp_path / "config.json"),
            example_path=str(tmp_path / "config.example.json"),
            prophet_email=None,
            email_provider=None,
        )


def test_bootstrap_result_carries_resolved_path(skill_root: Path) -> None:
    target = skill_root / "config.json"
    result = bootstrap_config_if_missing(
        config_path=str(target),
        example_path=str(skill_root / "config.example.json"),
        prophet_email=None,
        email_provider=None,
    )
    assert isinstance(result, BootstrapResult)
    assert result.config_path == str(target)
