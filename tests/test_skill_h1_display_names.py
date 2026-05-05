"""Every published skill must expose its display name as the first H1."""

from __future__ import annotations

from pathlib import Path
import re

REPO_ROOT = Path(__file__).resolve().parent.parent
ALLOWED_FRONTMATTER_FIELDS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#") or line[0].isspace():
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip('"').strip("'")
        out[key.strip()] = value
    return out


def _body(text: str) -> str:
    return re.sub(r"^---\r?\n[\s\S]*?\r?\n---\r?\n?", "", text)


def _first_h1(text: str) -> str | None:
    for line in _body(text).splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def test_every_published_skill_uses_agent_skill_frontmatter_fields() -> None:
    missing_required: list[str] = []
    invalid_name: list[str] = []
    unexpected_fields: list[str] = []
    for skill_file in _iter_skill_files():
        rel_path = skill_file.relative_to(REPO_ROOT)
        rel = str(rel_path)
        fm = _frontmatter(skill_file.read_text(encoding="utf-8"))
        if not fm.get("name") or not fm.get("description"):
            missing_required.append(rel)
        if fm.get("name") != rel_path.parent.name or not SKILL_NAME_RE.fullmatch(fm.get("name", "")):
            invalid_name.append(rel)
        extra = sorted(set(fm) - ALLOWED_FRONTMATTER_FIELDS)
        if extra:
            unexpected_fields.append(f"{rel}: {', '.join(extra)}")
    assert not missing_required, (
        f"{len(missing_required)} skills missing required name/description fields:\n"
        + "\n".join(f"  - {p}" for p in sorted(missing_required))
    )
    assert not invalid_name, (
        f"{len(invalid_name)} skills have invalid or directory-mismatched names:\n"
        + "\n".join(f"  - {p}" for p in sorted(invalid_name))
    )
    assert not unexpected_fields, (
        f"{len(unexpected_fields)} skills use non-spec top-level frontmatter fields:\n"
        + "\n".join(f"  - {p}" for p in sorted(unexpected_fields))
    )


def test_every_published_skill_uses_h1_display_name() -> None:
    missing: list[str] = []
    nonstandard: list[str] = []
    nonstandard_field = "display" "-name"
    for skill_file in _iter_skill_files():
        rel = str(skill_file.relative_to(REPO_ROOT))
        body = skill_file.read_text(encoding="utf-8")
        fm = _frontmatter(body)
        if fm.get(nonstandard_field):
            nonstandard.append(rel)
        if not _first_h1(body):
            missing.append(rel)
    assert not nonstandard, (
        f"{len(nonstandard)} skills still declare nonstandard display name field:\n"
        + "\n".join(f"  - {p}" for p in sorted(nonstandard))
    )
    assert not missing, (
        f"{len(missing)} skills missing first H1 display name:\n"
        + "\n".join(f"  - {p}" for p in sorted(missing))
    )
