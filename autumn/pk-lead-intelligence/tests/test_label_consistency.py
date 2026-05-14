"""Regression guard: SKILL.md must disclose pragma-no-cover stubs.

Issue #563 — Phase 3 (#550) and Phase 4 (#557) shipped as
`feat(...): Phase N live ...` titles while the live Salesforce
Lightning UI driving stayed behind `# pragma: no cover` functions
that `raise NotImplementedError`. SKILL.md did not surface that
gap, so a reader concluded live writes worked end-to-end when
they did not.

This test fails the build if the skill ever again ships a
pragma-no-cover NotImplementedError stub in `scripts/sf/` without
SKILL.md disclosing it via a `Status by Phase` section and an
`operator checkpoint` reference.
"""

from __future__ import annotations

import ast
from pathlib import Path

SKILL_DIR = Path(__file__).parent.parent
SF_DIR = SKILL_DIR / "scripts" / "sf"
SKILL_MD = SKILL_DIR / "SKILL.md"


def _function_raises_not_implemented(func: ast.FunctionDef) -> bool:
    """Walk the function body looking for `raise NotImplementedError`."""

    for stmt in ast.walk(func):
        if not isinstance(stmt, ast.Raise) or stmt.exc is None:
            continue
        exc = stmt.exc
        if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
            return True
        if (
            isinstance(exc, ast.Call)
            and isinstance(exc.func, ast.Name)
            and exc.func.id == "NotImplementedError"
        ):
            return True
    return False


def _find_pragma_no_cover_stubs() -> list[tuple[str, str]]:
    """Return [(file_name, func_name)] for every function in
    `scripts/sf/` that is marked `# pragma: no cover` on or near
    its `def` line and raises `NotImplementedError`."""

    stubs: list[tuple[str, str]] = []
    for py_file in sorted(SF_DIR.glob("*.py")):
        source = py_file.read_text()
        if "pragma: no cover" not in source:
            continue
        lines = source.splitlines()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if not _function_raises_not_implemented(node):
                continue
            # Multi-line `def` signatures push the pragma past the
            # `def` line; look in a small window starting at `def`.
            window = "\n".join(lines[node.lineno - 1 : node.lineno + 6])
            if "pragma: no cover" in window:
                stubs.append((py_file.name, node.name))
    return stubs


def test_skill_md_discloses_operator_checkpoint_when_stubs_exist():
    """If pragma-no-cover NotImplementedError stubs exist in
    `scripts/sf/`, `SKILL.md` must surface that gap. Without this
    disclosure a reader concludes live writes work end-to-end when
    they do not."""

    stubs = _find_pragma_no_cover_stubs()
    if not stubs:
        # Vacuous pass — no stubs means nothing to disclose. The
        # test only enforces the disclosure when the gap exists.
        return

    skill_text = SKILL_MD.read_text()

    assert "Status by Phase" in skill_text, (
        f"SKILL.md must include a 'Status by Phase' section disclosing the "
        f"{len(stubs)} shipped stubs in scripts/sf/ that raise "
        f"NotImplementedError. See issue #563.\nStubs found: {stubs}"
    )

    assert "operator checkpoint" in skill_text.lower(), (
        "SKILL.md must reference the 'operator checkpoint' phrase — that is "
        "where the stubs above are scheduled to be filled in against a "
        "Salesforce sandbox org. See issue #563."
    )
