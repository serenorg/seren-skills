"""Verify serendb_storage.py routes all API calls through the seren-db publisher,
not directly to /projects (which 404s on the Seren gateway)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

SERENDB_STORAGE_FILES: list[str] = [
    "polymarket/bot/scripts/serendb_storage.py",
]


def _read_source(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel_path", SERENDB_STORAGE_FILES, ids=SERENDB_STORAGE_FILES)
def test_no_direct_projects_route(rel_path: str) -> None:
    """serendb_storage must not hit /projects directly — must route through
    /publishers/seren-db/projects."""
    source = _read_source(rel_path)
    # Find lines that construct a URL with /projects but NOT /publishers/seren-db/projects
    for i, line in enumerate(source.splitlines(), 1):
        if "/projects" in line and "gateway_url" in line:
            assert "publishers/seren-db" in line, (
                f"{rel_path}:{i} hits /projects directly instead of "
                f"/publishers/seren-db/projects: {line.strip()}"
            )


@pytest.mark.parametrize("rel_path", SERENDB_STORAGE_FILES, ids=SERENDB_STORAGE_FILES)
def test_query_route_uses_publisher(rel_path: str) -> None:
    """SQL queries must go through /publishers/seren-db/query, not
    /projects/{pid}/branches/{bid}/query."""
    source = _read_source(rel_path)
    for i, line in enumerate(source.splitlines(), 1):
        if "/query" in line and "gateway_url" in line:
            assert "publishers/seren-db" in line, (
                f"{rel_path}:{i} hits /query directly instead of "
                f"/publishers/seren-db/query: {line.strip()}"
            )


@pytest.mark.parametrize("rel_path", SERENDB_STORAGE_FILES, ids=SERENDB_STORAGE_FILES)
def test_query_body_includes_project_and_branch(rel_path: str) -> None:
    """The query POST body must include project_id and branch_id since
    they are no longer encoded in the URL path."""
    source = _read_source(rel_path)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_execute_sql":
            func_source = ast.get_source_segment(source, node)
            assert "project_id" in func_source, (
                f"{rel_path}: _execute_sql body must include project_id"
            )
            assert "branch_id" in func_source, (
                f"{rel_path}: _execute_sql body must include branch_id"
            )
            break
    else:
        pytest.fail(f"{rel_path}: _execute_sql function not found")


@pytest.mark.parametrize("rel_path", SERENDB_STORAGE_FILES, ids=SERENDB_STORAGE_FILES)
def test_branch_lookup_accepts_production(rel_path: str) -> None:
    """Branch lookup must accept 'production' (Seren default) not just 'main'."""
    source = _read_source(rel_path)
    assert "production" in source, (
        f"{rel_path}: branch lookup must accept 'production' as a default branch name"
    )
