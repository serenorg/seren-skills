"""Critical catalog-wide branding test (issue #433).

ONE test file — walks every family-office leaf + router and asserts:

  1. The first top-level H1 is `Family Office · <something non-empty>` for
     leaves and matches a canonical map for routers.
  2. description leads with `Family office: `.
  3. frontmatter `name` matches the parent folder slug.
  4. Leaves keep a valid agent.py `PILLAR` constant.
  5. `knowledge/` is untouched (its SKILL.md is not required to match
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

KNOWN_PILLARS = frozenset(PILLAR_ROUTER_PILLARS.values())

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


def _first_top_level_h1(md: str) -> str:
    for line in md.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            return line[2:].strip()
    raise AssertionError("missing top-level H1 display name")


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

    # 1. H1 display name branding.
    display = _first_top_level_h1(md)
    if is_router:
        assert display == CANONICAL_ROUTER_DISPLAY[slug], (
            f"{slug}: router H1 display name must match canonical map; "
            f"got {display!r}"
        )
    else:
        assert display.startswith(DISPLAY_PREFIX), (
            f"{slug}: leaf H1 display name must start with {DISPLAY_PREFIX!r}; "
            f"got {display!r}"
        )
        # Reject the double-prefix regression seen during augmentation.
        assert not display.startswith(DISPLAY_PREFIX + DISPLAY_PREFIX), (
            f"{slug}: H1 display name double-prefixed"
        )
        assert len(display) > len(DISPLAY_PREFIX), (
            f"{slug}: H1 display name has prefix but no artifact name"
        )

    # 2. description leads with Family office:.
    desc = fm.get("description", "")
    assert desc.startswith(DESC_PREFIX), (
        f"{slug}: description must start with {DESC_PREFIX!r}; got {desc!r}"
    )
    assert not desc.startswith(DESC_PREFIX + DESC_PREFIX), (
        f"{slug}: description double-prefixed"
    )

    # 3. Agent Skills frontmatter name matches the folder slug.
    assert fm.get("name") == slug, (
        f"{slug}: frontmatter name must match folder slug; got {fm.get('name')!r}"
    )

    # 4. Leaf pillar source of truth still exists in code.
    if not is_router:
        pillar = _pillar_from_agent(skill_dir)
        assert pillar in KNOWN_PILLARS, (
            f"{slug}: agent.py pillar must be one of {sorted(KNOWN_PILLARS)!r}; "
            f"got {pillar!r}"
        )


def test_knowledge_skill_is_not_rebranded() -> None:
    """Explicit guard: knowledge keeps its existing branding, untouched."""
    knowledge_md = FAMILY_OFFICE_ROOT / "knowledge" / "SKILL.md"
    assert knowledge_md.exists()
    display = _first_top_level_h1(knowledge_md.read_text(encoding="utf-8"))
    assert not display.startswith(DISPLAY_PREFIX), (
        "knowledge skill must NOT be rebranded — it ships with its original "
        "H1 display name per the catalog's 'knowledge untouched' invariant"
    )
