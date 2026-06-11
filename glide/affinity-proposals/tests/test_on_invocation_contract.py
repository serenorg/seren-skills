"""Critical test for the chat-invocation contract (#969).

The loading agent only reads SKILL.md and skill.spec.yaml. If the
normative "On invocation" contract is absent, missing key phrases, or
buried below the engineer-facing Setup section, the agent regresses to
menu/terminal behavior that the operator (Cristin) cannot use.

This test is intentionally text-shaped: the contract IS the documentation.
"""

from __future__ import annotations

import re
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = SKILL_ROOT / "SKILL.md"
SKILL_SPEC = SKILL_ROOT / "skill.spec.yaml"


def _read_skill_md() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def _spec_description() -> str:
    text = SKILL_SPEC.read_text(encoding="utf-8")
    match = re.search(r"^\s{2}description:\s*(.+)$", text, flags=re.MULTILINE)
    assert match, "skill.spec.yaml must define skill.description"
    return match.group(1).strip().strip('"').strip("'")


def test_on_invocation_section_precedes_setup() -> None:
    body = _read_skill_md()
    invocation_idx = body.find("## On invocation")
    setup_idx = body.find("## Setup")
    assert invocation_idx != -1, "SKILL.md must declare a '## On invocation' section"
    assert setup_idx != -1, "SKILL.md must keep a '## Setup' section"
    assert invocation_idx < setup_idx, (
        "'## On invocation' must appear before '## Setup' so the loading agent "
        "reads the operator contract before any engineer instructions."
    )


def test_on_invocation_forbids_menu_and_terminal() -> None:
    body = _read_skill_md().lower()
    forbidden_phrases = [
        "do not offer a menu",
        "do not propose a terminal command",
        "cannot use a terminal",
    ]
    for phrase in forbidden_phrases:
        assert phrase in body, (
            f"SKILL.md must contain the normative phrase {phrase!r} so the "
            "loading agent does not regress to menu/CLI behavior."
        )


def test_on_invocation_drives_interview_in_chat() -> None:
    body = _read_skill_md()
    required_markers = [
        "config.json",
        "Question 1",
        "Affinity list",
        "mcp__seren-mcp__call_publisher",
        "mcp__seren-mcp__passwords_",
        "Write",
    ]
    for marker in required_markers:
        assert marker in body, (
            f"SKILL.md On invocation contract must reference {marker!r} so the "
            "loading agent knows it owns the interview and which MCP tools to call."
        )


def test_existing_config_defaults_to_dry_run_scan() -> None:
    body = _read_skill_md().lower()
    assert "default action is the dry-run scan" in body, (
        "SKILL.md must state that an existing config.json defaults the "
        "invocation to the dry-run scan, not a menu."
    )


def test_reconfigure_re_enters_chat_interview() -> None:
    body = _read_skill_md()
    assert "re-run setup" in body.lower(), (
        "SKILL.md must describe how 're-run setup' re-enters the chat interview."
    )
    assert "pre-fill" in body.lower() or "pre-filling" in body.lower(), (
        "SKILL.md must say the re-run pre-fills current answers as defaults."
    )


def test_setup_steps_moved_into_engineers_section() -> None:
    body = _read_skill_md()
    engineers_idx = body.find("## For engineers")
    assert engineers_idx != -1, (
        "SKILL.md must place engineer setup steps under a '## For engineers' "
        "section so the operator-facing path leads."
    )
    engineers_block = body[engineers_idx:]
    assert "scripts.agent --setup" in engineers_block, (
        "The engineer fallback `python -m scripts.agent --setup` must live in "
        "the For engineers section."
    )


def test_skill_spec_description_carries_invocation_contract() -> None:
    description = _spec_description().lower()
    assert "chat" in description, (
        "skill.spec.yaml description must reference the chat-interview "
        "invocation contract so the skill registry index surfaces it."
    )
    assert "config.json" in description, (
        "skill.spec.yaml description must reference config.json so the "
        "registry surfaces the no-config first-run contract."
    )
