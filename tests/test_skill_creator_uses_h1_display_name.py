"""skill-creator must tell authors to use the first H1 as the display name."""

from __future__ import annotations

from pathlib import Path

SKILL_CREATOR = (
    Path(__file__).resolve().parent.parent
    / "seren"
    / "skill-creator"
    / "SKILL.md"
)


def test_skill_creator_uses_h1_display_name() -> None:
    body = SKILL_CREATOR.read_text(encoding="utf-8")
    nonstandard_field = "display" "-name"
    assert nonstandard_field not in body, (
        "seren/skill-creator/SKILL.md should not tell authors to use "
        "the nonstandard display name frontmatter field."
    )
    assert "frontmatter must include `name` and `description`" in body, (
        "skill-creator should require only the spec frontmatter fields "
        "that this repo needs."
    )
    h1_phrase = "use the first `# H1` in the document body as the human-readable display name"
    assert h1_phrase in body, (
        "skill-creator should tell authors to put the display name in "
        "the first top-level markdown heading."
    )
