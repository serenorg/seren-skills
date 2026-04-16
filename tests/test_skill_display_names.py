"""Every published skill must declare a display-name in its frontmatter.

Fixes #373: 19 of 74 skills in the R2 catalog index were missing
`display-name`, which surfaces the raw directory basename in SerenDesktop
(seren-desktop#1469). The build-index.mjs walker includes every
org/skill/SKILL.md path; we mirror that walker here so the guardrail
matches what actually ships to Cloudflare R2.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Known-missing skills tracked in separate issues. Adding display-name to
# gdex/trading currently trips the trading-skill-safety validator because
# that SKILL.md pre-dates the execution-safety contract; fixing it is a
# separate scope. See the gdex/trading exemption issue.
EXEMPT = {
    "gdex/trading/SKILL.md",  # TODO(#408): unblock safety contract, then add display-name
}


def _iter_skill_files() -> list[Path]:
    skills: list[Path] = []
    for path in REPO_ROOT.rglob("SKILL.md"):
        rel_parts = path.relative_to(REPO_ROOT).parts
        # build-index.mjs only indexes org/skill/SKILL.md (3 parts).
        if len(rel_parts) != 3:
            continue
        if rel_parts[0].startswith(".") or rel_parts[0] == "node_modules":
            continue
        skills.append(path)
    return skills


def _frontmatter(text: str) -> dict[str, str]:
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}
    end = stripped.find("---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    for line in stripped[3:end].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip('"').strip("'")
        out[key.strip()] = value
    return out


def test_every_published_skill_has_display_name() -> None:
    missing: list[str] = []
    for skill_file in _iter_skill_files():
        rel = str(skill_file.relative_to(REPO_ROOT))
        if rel in EXEMPT:
            continue
        fm = _frontmatter(skill_file.read_text(encoding="utf-8"))
        if not fm.get("display-name"):
            missing.append(rel)
    assert not missing, (
        f"{len(missing)} skills missing display-name in frontmatter:\n"
        + "\n".join(f"  - {p}" for p in sorted(missing))
    )
