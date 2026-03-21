from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REMOVED_SKILL = "polymarket/paired-market-basis-maker"
REGISTRY_TESTS = [
    "tests/test_optimizer_candidates.py",
    "tests/test_on_invoke_directive.py",
    "tests/test_config_bootstrap_runtime.py",
    "polymarket/tests/test_execution_safety.py",
]


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


def test_removed_skill_not_pinned_in_repo_side_skill_registries() -> None:
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

