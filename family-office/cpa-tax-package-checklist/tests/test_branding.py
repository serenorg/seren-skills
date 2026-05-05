"""Critical catalog-wide branding test (issue #433).

ONE test file — walks every family-office leaf + router and asserts:

  1. display-name is `Family Office · <something non-empty>` for leaves
     and matches a canonical map for routers.
  2. description leads with `Family office: `.
  3. tags contains `family-office`.
  4. tags contains exactly one `pillar:<x>` tag for leaves and pillar
     routers; the top-level router omits it.
  5. Leaf's tag pillar matches the leaf's agent.py `PILLAR` constant
     (source of truth, so tags cannot silently drift from code).
  6. Routers carry `type:router`.
  7. `knowledge/` is untouched (its SKILL.md is not required to match
     the family-office branding).

Per-leaf duplication of this test would catch no new bugs. Living
inside cpa-tax-package-checklist/tests/ because the reference leaf is
where catalog-wide assertions live in this repo (matches test_sinks.py
and test_comms.py).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
FAMILY_OFFICE_ROOT = HERE.parent.parent  # .../family-office

DISPLAY_PREFIX = "Family Office · "
DESC_PREFIX = "Family office: "

CANONICAL_ROUTER_DISPLAY = {
    "router": "Family Office · Top-Level Router",
    "capital-allocation-router": "Family Office · Capital Allocation Router",
    "complexity-management-router": "Family Office · Complexity Management Router",
    "legacy-preservation-router": "Family Office · Legacy Preservation Router",
}

PILLAR_ROUTER_PILLARS = {
    "capital-allocation-router": "capital-allocation",
    "complexity-management-router": "complexity-management",
    "legacy-preservation-router": "legacy-preservation",
}

SKIP_DIRS = {"knowledge"}


def _parse_frontmatter(md: str) -> dict[str, str]:
    lines = md.split("\n")
    assert lines and lines[0].strip() == "---", "missing frontmatter opener"
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        m = re.match(r"^([A-Za-z][A-Za-z0-9_\-]*):\s*(.*)$", line)
        if not m:
            continue
        k = m.group(1).strip()
        v = m.group(2).strip()
        if (v.startswith('"') and v.endswith('"')) or (
            v.startswith("'") and v.endswith("'")
        ):
            v = v[1:-1]
        out[k] = v
    raise AssertionError("missing frontmatter closer")


def _parse_tags(value: str) -> list[str]:
    v = value.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1]
    else:
        inner = v
    return [t.strip().strip("\"'") for t in inner.split(",") if t.strip()]


def _pillar_from_agent(leaf_dir: Path) -> str:
    agent = (leaf_dir / "scripts" / "agent.py").read_text(encoding="utf-8")
    m = re.search(r'^PILLAR\s*=\s*"([^"]+)"', agent, re.MULTILINE)
    assert m, f"cannot read PILLAR from {leaf_dir.name}/scripts/agent.py"
    return m.group(1)


def _catalog() -> dict[str, Path]:
    """Return {slug: skill_dir} for every family-office skill except knowledge."""
    out: dict[str, Path] = {}
    for entry in sorted(FAMILY_OFFICE_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        if entry.name in SKIP_DIRS:
            continue
        if not (entry / "SKILL.md").exists():
            continue
        out[entry.name] = entry
    return out


def test_catalog_has_56_leaves_and_4_routers() -> None:
    catalog = _catalog()
    assert len(catalog) == 60, (
        f"expected 56 leaves + 4 routers = 60 branded skills; got {len(catalog)}"
    )


def _leaf_and_router_params():
    out = []
    for slug, skill_dir in _catalog().items():
        is_router = slug in CANONICAL_ROUTER_DISPLAY
        out.append(pytest.param(slug, skill_dir, is_router, id=slug))
    return out


@pytest.mark.parametrize("slug,skill_dir,is_router", _leaf_and_router_params())
def test_skill_branded_correctly(slug: str, skill_dir: Path, is_router: bool) -> None:
    md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    fm = _parse_frontmatter(md)

    # 1. display-name branding.
    display = fm.get("display-name", "")
    if is_router:
        assert display == CANONICAL_ROUTER_DISPLAY[slug], (
            f"{slug}: router display-name must match canonical map; "
            f"got {display!r}"
        )
    else:
        assert display.startswith(DISPLAY_PREFIX), (
            f"{slug}: leaf display-name must start with {DISPLAY_PREFIX!r}; "
            f"got {display!r}"
        )
        # Reject the double-prefix regression seen during augmentation.
        assert not display.startswith(DISPLAY_PREFIX + DISPLAY_PREFIX), (
            f"{slug}: display-name double-prefixed"
        )
        assert len(display) > len(DISPLAY_PREFIX), (
            f"{slug}: display-name has prefix but no artifact name"
        )

    # 2. description leads with Family office:.
    desc = fm.get("description", "")
    assert desc.startswith(DESC_PREFIX), (
        f"{slug}: description must start with {DESC_PREFIX!r}; got {desc!r}"
    )
    assert not desc.startswith(DESC_PREFIX + DESC_PREFIX), (
        f"{slug}: description double-prefixed"
    )

    # 3. tags — family-office always present.
    tags = _parse_tags(fm.get("tags", ""))
    assert "family-office" in tags, f"{slug}: tags missing 'family-office'"

    # 4. Pillar tag correctness.
    pillar_tags = [t for t in tags if t.startswith("pillar:")]
    if slug == "router":
        assert pillar_tags == [], f"{slug}: top-level router must NOT carry a pillar tag"
    elif slug in PILLAR_ROUTER_PILLARS:
        expected = f"pillar:{PILLAR_ROUTER_PILLARS[slug]}"
        assert pillar_tags == [expected], (
            f"{slug}: expected {expected!r}; got {pillar_tags!r}"
        )
    else:
        # Leaf: must have exactly one pillar tag, and it must match the
        # agent.py PILLAR constant (source of truth, not an inferred guess).
        assert len(pillar_tags) == 1, (
            f"{slug}: leaf must have exactly one pillar tag; got {pillar_tags!r}"
        )
        expected = f"pillar:{_pillar_from_agent(skill_dir)}"
        assert pillar_tags[0] == expected, (
            f"{slug}: tag {pillar_tags[0]!r} does not match agent.py pillar "
            f"{expected!r} — tags would silently drift"
        )

    # 5. Routers carry type:router.
    if is_router:
        assert "type:router" in tags, f"{slug}: router missing 'type:router' tag"
    else:
        assert "type:router" not in tags, (
            f"{slug}: leaf must not carry 'type:router' tag"
        )


def test_knowledge_skill_is_not_rebranded() -> None:
    """Explicit guard: knowledge keeps its existing branding, untouched."""
    knowledge_md = FAMILY_OFFICE_ROOT / "knowledge" / "SKILL.md"
    assert knowledge_md.exists()
    fm = _parse_frontmatter(knowledge_md.read_text(encoding="utf-8"))
    display = fm.get("display-name", "")
    assert not display.startswith(DISPLAY_PREFIX), (
        "knowledge skill must NOT be rebranded — it ships with its original "
        "display-name per the catalog's 'knowledge untouched' invariant"
    )
