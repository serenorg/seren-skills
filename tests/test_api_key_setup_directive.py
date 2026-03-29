"""Verify all Taariq-authored skills that reference SEREN_API_KEY
include the API Key Setup section with existing-auth checks before registration."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Skills authored by Taariq that reference SEREN_API_KEY
TAARIQ_SKILLS: list[str] = [
    "polymarket/maker-rebate-bot",
    "polymarket/liquidity-paired-basis-maker",
    "polymarket/high-throughput-paired-basis-maker",
    "polymarket/bot",
    "kraken/grid-trader",
    "kraken/smart-dca-bot",
    "kraken/money-mode-router",
    "kraken/1099-da-tax-reconciler",
    "kraken/carf-dac8-crypto-asset-reporting",
    "coinbase/grid-trader",
    "coinbase/smart-dca-bot",
    "alpaca/saas-short-trader",
    "alpaca/sass-short-trader-delta-neutral",
    "alphagrowth/euler-base-vault-bot",
    "spectra/spectra-pt-yield-trader",
    "wellsfargo/bank-statement-processing",
    "crypto-bullseye-zone/tax",
    "apollo/api",
    "curve/curve-gauge-yield-trader",
    "prophet/prophet-growth-agent",
    "prophet/prophet-adversarial-auditor",
    "prophet/prophet-market-seeder",
    "sidepit/auction-trader",
]

# Skills that have the full ## API Key Setup section (not just a docs link)
SKILLS_WITH_SETUP_SECTION: list[str] = [
    s for s in TAARIQ_SKILLS
    if s not in {
        "curve/curve-gauge-yield-trader",
        "prophet/prophet-growth-agent",
        "prophet/prophet-adversarial-auditor",
        "prophet/prophet-market-seeder",
    }
]


def _read_skill(rel: str) -> str:
    return (REPO_ROOT / rel / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("skill", TAARIQ_SKILLS, ids=TAARIQ_SKILLS)
def test_skill_has_api_key_setup_or_docs_link(skill: str) -> None:
    """Every Taariq skill must tell the agent how to obtain a SEREN_API_KEY."""
    content = _read_skill(skill)
    has_section = "## API Key Setup" in content
    has_docs_link = "docs.serendb.com/skills.md" in content
    assert has_section or has_docs_link, (
        f"{skill}/SKILL.md has no API Key Setup section and no docs.serendb.com/skills.md link"
    )


@pytest.mark.parametrize("skill", TAARIQ_SKILLS, ids=TAARIQ_SKILLS)
def test_api_key_setup_has_registration_endpoint(skill: str) -> None:
    """The setup section must include the auto-registration endpoint or docs link."""
    content = _read_skill(skill)
    has_register = "api.serendb.com/auth/agent" in content
    has_docs = "docs.serendb.com/skills.md" in content
    assert has_register or has_docs, (
        f"{skill}/SKILL.md missing registration endpoint or docs link"
    )


@pytest.mark.parametrize("skill", SKILLS_WITH_SETUP_SECTION, ids=SKILLS_WITH_SETUP_SECTION)
def test_api_key_setup_checks_existing_auth_before_register(skill: str) -> None:
    """The setup section must check for existing Seren Desktop / .env / shell
    auth BEFORE telling the agent to register a new account (#255)."""
    content = _read_skill(skill)
    assert "Seren Desktop" in content, (
        f"{skill}/SKILL.md API Key Setup does not mention Seren Desktop auth check"
    )
    # The check-existing text must appear BEFORE the register curl
    check_pos = content.find("If set, no further action is needed")
    register_pos = content.find("api.serendb.com/auth/agent")
    assert check_pos != -1, (
        f"{skill}/SKILL.md missing 'no further action' existing-auth guard"
    )
    assert register_pos != -1, (
        f"{skill}/SKILL.md missing registration endpoint"
    )
    assert check_pos < register_pos, (
        f"{skill}/SKILL.md: existing-auth check must come BEFORE registration"
    )


@pytest.mark.parametrize("skill", SKILLS_WITH_SETUP_SECTION, ids=SKILLS_WITH_SETUP_SECTION)
def test_api_key_setup_warns_against_duplicate_accounts(skill: str) -> None:
    """The setup section must warn that creating a duplicate account
    overrides the user's funded account (#255)."""
    content = _read_skill(skill)
    assert "Do not create a new account if a key already exists" in content, (
        f"{skill}/SKILL.md missing duplicate-account warning"
    )
