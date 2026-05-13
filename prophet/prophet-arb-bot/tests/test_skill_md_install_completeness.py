"""Issue #515: Desktop reports `Incomplete install: prophet-arb-bot` because
SKILL.md references files that do not ship in the bundle.

Pin the contract: every backticked, payload-extension path in SKILL.md
must exist on disk relative to the skill root — mirroring
`seren-desktop/src-tauri/src/skills.rs::extract_referenced_files`. If a
future SKILL.md edit pins a new fixture/script/config path without
committing the file, this test fails before it reaches Desktop.

The regex deliberately copies the Rust validator's payload-extension
allowlist (.py .sh .json .txt .toml .yaml .yml .js .ts) so the seren-skills
test and the Desktop post-install check agree on what counts as a
"referenced file".
"""

from __future__ import annotations

import re
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = SKILL_ROOT / "SKILL.md"

# Mirror of seren-desktop/src-tauri/src/skills.rs::extract_referenced_files
# backtick regex. Same allowlist of payload extensions.
_BACKTICK_PATH_RE = re.compile(
    r"`([a-zA-Z0-9_./-]+\.(?:py|sh|json|txt|toml|yaml|yml|js|ts))`"
)

# Template-sibling suffixes that the Desktop validator treats as
# user-provisioned (so a missing config.json next to config.example.json
# is not "missing"). Mirror `has_template_sibling` in skills.rs.
_TEMPLATE_INFIXES = ("example", "template", "sample")


def _has_template_sibling(rel_path: str) -> bool:
    rel = Path(rel_path)
    parent = SKILL_ROOT / (rel.parent if rel.parent != Path("") else Path("."))
    name = rel.name
    # Pattern A: `.env` -> `.env.example`
    for infix in _TEMPLATE_INFIXES:
        if (parent / f"{name}.{infix}").exists():
            return True
    # Pattern B: `config.json` -> `config.example.json`
    if rel.suffix:
        stem = rel.stem
        ext = rel.suffix.lstrip(".")
        for infix in _TEMPLATE_INFIXES:
            if (parent / f"{stem}.{infix}.{ext}").exists():
                return True
    return False


def test_skill_md_backticked_payload_paths_all_exist() -> None:
    content = SKILL_MD.read_text(encoding="utf-8")
    referenced = {m.group(1) for m in _BACKTICK_PATH_RE.finditer(content)}
    assert referenced, "expected at least one backticked payload path in SKILL.md"

    missing = sorted(
        path
        for path in referenced
        if not (SKILL_ROOT / path).exists() and not _has_template_sibling(path)
    )
    assert not missing, (
        "SKILL.md references payload files that do not ship in the bundle "
        "— Desktop's post-install validator will flag these as "
        f"`Incomplete install` (issue #515): {missing}"
    )
