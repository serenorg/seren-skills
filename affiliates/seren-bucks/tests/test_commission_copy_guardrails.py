"""Critical guardrails for SerenBucks outreach copy correctness.

These tests cover the P0/P1 fixes tracked in #400-#404. They assert the
skill's static guidance tells Claude how to describe the 3-tier unilevel
program and uses the correct production tracked-link domain. Runtime
email synthesis is Claude's job; these tests lock the contract that feeds
it.
"""

from __future__ import annotations

import json
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
EMAIL_TEMPLATE = SKILL_ROOT / "references" / "email-templates.md"
SKILL_MD = SKILL_ROOT / "SKILL.md"
COMMON_PY = SKILL_ROOT / "scripts" / "common.py"
CONFIG_EXAMPLE = SKILL_ROOT / "config.example.json"
SKILL_SPEC = SKILL_ROOT / "skill.spec.yaml"


def test_email_template_documents_three_step_recruitment_flow() -> None:
    """P0 #400: drafts must describe join -> get own code -> share own code."""
    body = EMAIL_TEMPLATE.read_text(encoding="utf-8").lower()
    assert "join" in body, "template must tell recipient to join first"
    assert "your own" in body and "code" in body, (
        "template must explain recipient gets their own SRN_ code after signup"
    )
    assert "share" in body, "template must tell recipient to share THEIR code"
    assert "20%" in body, "template must state the 20% direct commission"
    assert "5%" in body, "template must state the 5% override so sender's stake is honest"


def test_skill_md_documents_three_tier_unilevel_structure() -> None:
    """P0 #401: SKILL.md must ground Claude in tier 0/1/2 commission model."""
    body = SKILL_MD.read_text(encoding="utf-8").lower()
    for phrase in ("tier 0", "tier 1", "tier 2"):
        assert phrase in body, f"SKILL.md missing '{phrase}' reference"
    assert "20%" in body and "5%" in body, (
        "SKILL.md must document 20% direct and 5% override rates"
    )
    assert "email-templates" in body, (
        "SKILL.md must point Claude at references/email-templates.md"
    )


def test_default_tracked_link_uses_serendb_domain() -> None:
    """P1 #403: all defaults must use serendb.com, not the obsolete seren.ai URL."""
    offenders = []
    for path in (COMMON_PY, CONFIG_EXAMPLE, SKILL_SPEC):
        if "seren.ai/serenbucks" in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert not offenders, f"obsolete seren.ai/serenbucks domain in: {offenders}"

    config = json.loads(CONFIG_EXAMPLE.read_text(encoding="utf-8"))
    for link in (
        config["program"]["tracked_link"],
        config["inputs"]["tracked_link"],
    ):
        assert link.startswith("https://serendb.com"), (
            f"default tracked_link must use serendb.com, got: {link}"
        )


def test_signoff_does_not_attribute_sender_to_serendb() -> None:
    """#417: the sender is an affiliate, not a SerenDB employee.
    The sign-off must never carry a company attribution line."""
    import re

    body = EMAIL_TEMPLATE.read_text(encoding="utf-8")
    signoff = re.search(
        r"Cheers,\s*\n\s*\{\{sender_full_name\}\}\s*\n(.*?)```",
        body,
        re.DOTALL,
    )
    assert signoff, "cannot locate sign-off block in template"
    after_name = signoff.group(1).strip()
    assert after_name == "", (
        f"sign-off has extra content after sender_full_name: {after_name!r}. "
        "Affiliates must sign as themselves, not as SerenDB."
    )
