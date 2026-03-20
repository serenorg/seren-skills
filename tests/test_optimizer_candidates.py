"""Verify optimization candidate functions return >= 14 candidates
with unique names so max_iterations=15 is fully utilized."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SKILLS: list[tuple[str, str]] = [
    ("polymarket/maker-rebate-bot/scripts/agent.py", "_maker_optimization_candidates"),
    ("polymarket/liquidity-paired-basis-maker/scripts/agent.py", "_pair_optimization_candidates"),
    ("polymarket/high-throughput-paired-basis-maker/scripts/agent.py", "_pair_optimization_candidates"),
    ("polymarket/paired-market-basis-maker/scripts/agent.py", "_pair_optimization_candidates"),
]

MIN_CANDIDATES = 14  # 1 baseline + 14 candidates = 15 max_iterations


def _extract_candidate_list(filepath: Path, func_name: str) -> ast.List:
    """AST-extract the return list from the optimization function."""
    tree = ast.parse(filepath.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and isinstance(child.value, ast.List):
                    return child.value
    raise AssertionError(f"{func_name} not found or has no return list in {filepath}")


def _extract_candidate_names(filepath: Path, func_name: str) -> list[str]:
    """Extract candidate 'name' strings from the AST."""
    lst = _extract_candidate_list(filepath, func_name)
    names: list[str] = []
    for elt in lst.elts:
        if not isinstance(elt, ast.Dict):
            continue
        for key, val in zip(elt.keys, elt.values):
            if isinstance(key, ast.Constant) and key.value == "name" and isinstance(val, ast.Constant):
                names.append(val.value)
    return names


@pytest.mark.parametrize("rel_path,func_name", SKILLS, ids=[s[0].split("/")[0] + "/" + s[0].split("/")[1] for s in SKILLS])
def test_candidate_count_at_least_14(rel_path: str, func_name: str) -> None:
    """Optimizer must have >= 14 candidates to support max_iterations=15."""
    filepath = REPO_ROOT / rel_path
    lst = _extract_candidate_list(filepath, func_name)
    assert len(lst.elts) >= MIN_CANDIDATES, (
        f"{rel_path}: {func_name}() returns {len(lst.elts)} candidates, need >= {MIN_CANDIDATES}"
    )


@pytest.mark.parametrize("rel_path,func_name", SKILLS, ids=[s[0].split("/")[0] + "/" + s[0].split("/")[1] for s in SKILLS])
def test_candidate_names_unique(rel_path: str, func_name: str) -> None:
    """All candidate names must be unique to avoid confusion in results."""
    filepath = REPO_ROOT / rel_path
    names = _extract_candidate_names(filepath, func_name)
    dupes = [n for n in names if names.count(n) > 1]
    assert not dupes, f"{rel_path}: duplicate candidate names: {set(dupes)}"


@pytest.mark.parametrize("rel_path,func_name", SKILLS, ids=[s[0].split("/")[0] + "/" + s[0].split("/")[1] for s in SKILLS])
def test_every_candidate_has_name_and_subset_size(rel_path: str, func_name: str) -> None:
    """Every candidate dict must have 'name' and 'subset_size' keys."""
    filepath = REPO_ROOT / rel_path
    lst = _extract_candidate_list(filepath, func_name)
    for i, elt in enumerate(lst.elts):
        assert isinstance(elt, ast.Dict), f"Candidate {i} is not a dict"
        keys = [k.value for k in elt.keys if isinstance(k, ast.Constant)]
        assert "name" in keys, f"Candidate {i} missing 'name'"
        assert "subset_size" in keys, f"Candidate {i} missing 'subset_size'"
