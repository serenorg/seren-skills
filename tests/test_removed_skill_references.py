"""Verify removed skills stay removed — no directory, no references, no Desktop exposure."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REMOVED_SKILL = "polymarket/paired-market-basis-maker"
REMOVED_SLUG = "paired-market-basis-maker"
REGISTRY_TESTS = [
    "tests/test_optimizer_candidates.py",
    "tests/test_on_invoke_directive.py",
    "tests/test_config_bootstrap_runtime.py",
    "polymarket/tests/test_execution_safety.py",
]

# File extensions that could contain skill references
SCANNABLE_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml"}

# Files that legitimately mention the removed skill (this test file itself,
# and strategy descriptions in sibling skills that reference the original approach)
ALLOWLISTED_FILES = {
    "tests/test_removed_skill_references.py",
}

SKIP_DIRS = {".git", ".worktrees", "__pycache__", ".venv", "node_modules", ".pytest_cache"}


def _literal_collections(module_path: Path) -> list[list[str] | tuple[str, ...] | dict[str, str]]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    literals: list[list[str] | tuple[str, ...] | dict[str, str]] = []

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except Exception:
            continue
        if isinstance(value, (list, tuple, dict)):
            literals.append(value)

    return literals


def _iter_string_values(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_string_values(item)
    elif isinstance(value, dict):
        for item in value.keys():
            yield from _iter_string_values(item)
        for item in value.values():
            yield from _iter_string_values(item)


def test_removed_skill_directory_does_not_exist() -> None:
    """The skill directory must not exist in the repo."""
    skill_dir = REPO_ROOT / REMOVED_SKILL
    assert not skill_dir.exists(), (
        f"{REMOVED_SKILL}/ directory still exists — delete it to complete removal."
    )


def test_removed_skill_not_pinned_in_repo_side_skill_registries() -> None:
    """Skill registries (test lists, workflow configs) must not reference the removed skill."""
    for relative_path in REGISTRY_TESTS:
        module_path = REPO_ROOT / relative_path
        string_values = [
            value
            for literal in _literal_collections(module_path)
            for value in _iter_string_values(literal)
        ]
        assert REMOVED_SKILL not in string_values, (
            f"{relative_path} still references removed skill {REMOVED_SKILL}"
        )


def test_removed_skill_not_referenced_in_workflows_or_configs() -> None:
    """No workflow, config, or documentation file should reference the removed skill
    as an active/discoverable skill. This catches Desktop skill catalog entries,
    CI workflow lists, and packaging manifests."""
    violations: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file():
            continue
        if any(skip in path.parts for skip in SKIP_DIRS):
            continue
        if path.suffix not in SCANNABLE_SUFFIXES:
            continue
        try:
            rel = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            continue
        if rel in ALLOWLISTED_FILES:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # Check for the full skill path (org/skill-name)
        if REMOVED_SKILL in content:
            violations.append(f"{rel} references '{REMOVED_SKILL}'")
    assert not violations, (
        "Removed skill is still referenced in repo files "
        "(these may expose it via Desktop skill sync):\n  "
        + "\n  ".join(violations)
    )
