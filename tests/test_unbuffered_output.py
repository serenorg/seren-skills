"""Verify all trading/scanning skills use unbuffered stdout for piped output."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

TRADING_SKILLS_WITH_AGENT_PY: list[str] = [
    "polymarket/bot",
    "polymarket/maker-rebate-bot",
    "polymarket/high-throughput-paired-basis-maker",
    "polymarket/liquidity-paired-basis-maker",
    "coinbase/grid-trader",
    "coinbase/smart-dca-bot",
    "kraken/grid-trader",
    "kraken/smart-dca-bot",
    "kraken/money-mode-router",
    "curve/curve-gauge-yield-trader",
    "spectra/spectra-pt-yield-trader",
    "alphagrowth/euler-base-vault-bot",
    "prophet/prophet-adversarial-auditor",
    "prophet/prophet-growth-agent",
    "prophet/prophet-market-seeder",
]


@pytest.mark.parametrize("skill", TRADING_SKILLS_WITH_AGENT_PY)
def test_agent_has_unbuffered_output(skill: str) -> None:
    agent_py = REPO_ROOT / skill / "scripts" / "agent.py"
    assert agent_py.exists(), f"{agent_py} not found"
    source = agent_py.read_text()
    assert "PYTHONUNBUFFERED" in source, (
        f"{skill}/scripts/agent.py is missing the unbuffered output fix. "
        "Add the PYTHONUNBUFFERED / reconfigure(line_buffering=True) block "
        "after 'import sys' so piped output is visible immediately."
    )
    assert "reconfigure(line_buffering=True)" in source, (
        f"{skill}/scripts/agent.py sets PYTHONUNBUFFERED but is missing "
        "sys.stdout.reconfigure(line_buffering=True) for in-process buffering."
    )
