"""Verify all Taariq-authored skills that reference SEREN_API_KEY
include the API Key Setup section with auto-registration instructions."""

from __future__ import annotations

import re
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
        f"{skill}/SKILL.md missing registration endpoint (api.serendb.com/auth/agent) or docs link"
    )


@pytest.mark.parametrize("skill", TAARIQ_SKILLS, ids=TAARIQ_SKILLS)
def test_no_generic_missing_key_without_remediation(skill: str) -> None:
    """Skills must not tell agents to just 'set SEREN_API_KEY' without
    providing the registration flow or docs link."""
    content = _read_skill(skill)
    has_register = "api.serendb.com/auth/agent" in content
    has_docs = "docs.serendb.com/skills.md" in content
    assert has_register or has_docs, (
        f"{skill}/SKILL.md references SEREN_API_KEY but has no registration or docs guidance"
    )
