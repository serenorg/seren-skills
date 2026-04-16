"""skill-creator must tell authors to set display-name in frontmatter.

Fixes #412: skill-creator SKILL.md used to instruct authors to use only
`name` + `description` and to derive the display name from the first H1.
Every skill scaffolded from that guidance shipped without
`display-name`, so the R2 catalog indexer fell back to raw slugs
(root cause of #373's recurrence).

This guardrail asserts the skill-creator authoring guidance actively
prescribes `display-name` — in the conventions prose, the baseline
template, the validation checklist, and the example skeleton.
"""

from __future__ import annotations

from pathlib import Path

SKILL_CREATOR = (
    Path(__file__).resolve().parent.parent
    / "seren"
    / "skill-creator"
    / "SKILL.md"
)


def test_skill_creator_requires_display_name_in_frontmatter() -> None:
    body = SKILL_CREATOR.read_text(encoding="utf-8")
    assert body.count("display-name") >= 4, (
        "seren/skill-creator/SKILL.md should mention `display-name` in "
        "(1) the conventions prose, (2) the baseline frontmatter template, "
        "(3) the validation checklist, and (4) the example skeleton. "
        f"Found {body.count('display-name')} mentions."
    )
    # The old guidance explicitly said to skip display-name; make sure that
    # sentence is gone so authors don't follow it.
    forbidden = "frontmatter uses only `name` and `description`"
    assert forbidden not in body, (
        f"skill-creator still contains the obsolete guidance: {forbidden!r}"
    )
    # The "use H1 as display name" line is also wrong — catalog reads frontmatter.
    h1_phrase = "use the first `# H1` in the body as the display name"
    assert h1_phrase not in body, (
        f"skill-creator still tells authors to derive display name from H1: "
        f"{h1_phrase!r}"
    )
