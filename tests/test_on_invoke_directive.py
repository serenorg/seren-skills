"""Verify every trading skill SKILL.md contains an On Invoke section
that instructs the LLM to auto-run the default action without presenting a menu."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every trading skill directory and its expected default action keyword.
TRADING_SKILLS: list[tuple[str, str]] = [
    ("polymarket/maker-rebate-bot", "backtest"),
    ("polymarket/liquidity-paired-basis-maker", "backtest"),
    ("polymarket/high-throughput-paired-basis-maker", "backtest"),
    ("polymarket/bot", "dry-run"),
    ("kraken/grid-trader", "dry-run"),
    ("kraken/smart-dca-bot", "dry-run"),
    ("coinbase/grid-trader", "dry-run"),
    ("coinbase/smart-dca-bot", "dry-run"),
    ("alpaca/saas-short-trader", "paper-sim"),
    ("alpaca/sass-short-trader-delta-neutral", "paper-sim"),
    ("curve/curve-gauge-yield-trader", "dry-run"),
    ("spectra/spectra-pt-yield-trader", "dry-run"),
    ("alphagrowth/euler-base-vault-bot", "dry-run"),
    ("sidepit/auction-trader", "dry-run"),
]


@pytest.mark.parametrize("skill_path,default_action", TRADING_SKILLS, ids=[s[0] for s in TRADING_SKILLS])
def test_skill_has_on_invoke_auto_run(skill_path: str, default_action: str) -> None:
    """Each trading skill must instruct the LLM to auto-run the default
    action immediately on invoke, not present a menu first."""
    skill_md = REPO_ROOT / skill_path / "SKILL.md"
    assert skill_md.exists(), f"{skill_md} not found"
    content = skill_md.read_text(encoding="utf-8").lower()

    # Must contain the auto-run directive (case-insensitive).
    assert "immediately run" in content, (
        f"{skill_path}/SKILL.md missing 'Immediately run' directive in On Invoke section"
    )
    assert "without asking" in content, (
        f"{skill_path}/SKILL.md missing 'without asking' directive — LLM may still present a menu"
    )
    assert "do not present a menu" in content or "do not present a menu or ask" in content, (
        f"{skill_path}/SKILL.md missing 'Do not present a menu' guard"
    )
    assert "after results are displayed" in content or "after results" in content, (
        f"{skill_path}/SKILL.md missing post-results menu instruction"
    )


@pytest.mark.parametrize("skill_path,default_action", TRADING_SKILLS, ids=[s[0] for s in TRADING_SKILLS])
def test_on_invoke_before_execution_modes(skill_path: str, default_action: str) -> None:
    """The On Invoke / auto-run directive must appear BEFORE the Execution Modes
    section so the LLM processes it first."""
    skill_md = REPO_ROOT / skill_path / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8").lower()

    invoke_pos = content.find("immediately run")
    modes_pos = content.find("## execution modes")

    # If there's no Execution Modes section, the ordering constraint is trivially met.
    if modes_pos == -1:
        return

    assert invoke_pos < modes_pos, (
        f"{skill_path}/SKILL.md: On Invoke directive appears AFTER Execution Modes — "
        "the LLM will see the mode list first and may present a menu"
    )
