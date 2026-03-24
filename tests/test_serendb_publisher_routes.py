"""Verify all SerenDB storage/bootstrap files route API calls through the
seren-db publisher, not directly to /projects (which 404s on the Seren gateway)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Files that build URLs by appending paths to a base URL (gateway_url or api_base).
# Each tuple: (file_path, url_building_marker)
# url_building_marker is the string that appears on lines that construct API URLs.
SERENDB_FILES: list[tuple[str, str]] = [
    ("polymarket/bot/scripts/serendb_storage.py", "gateway_url"),
    ("crypto-bullseye-zone/tax/scripts/serendb_store.py", "_api_base"),
    ("alpaca/saas-short-trader/scripts/serendb_bootstrap.py", "api_base"),
    ("alpaca/sass-short-trader-delta-neutral/scripts/serendb_bootstrap.py", "api_base"),
    # prophet skills bake /publishers/seren-db into api_base itself, so path
    # strings like /projects/... resolve correctly without prefixing.
    ("prophet/prophet-growth-agent/scripts/agent.py", "api_base"),
    ("prophet/prophet-adversarial-auditor/scripts/agent.py", "api_base"),
    ("prophet/prophet-market-seeder/scripts/agent.py", "api_base"),
]

IDS = [t[0] for t in SERENDB_FILES]


def _read_source(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _has_publisher_in_base(source: str) -> bool:
    """Check if the file bakes /publishers/seren-db into the base URL itself."""
    return "publishers/seren-db" in source.split("api_base")[0] if "api_base" in source else False


@pytest.mark.parametrize("rel_path,marker", SERENDB_FILES, ids=IDS)
def test_no_direct_projects_route(rel_path: str, marker: str) -> None:
    """SerenDB files must not hit /projects directly — must route through
    /publishers/seren-db/projects (either in path or base URL)."""
    source = _read_source(rel_path)
    # If the file bakes /publishers/seren-db into api_base, then bare /projects
    # paths are fine since the full URL will be correct.
    base_has_publisher = "publishers/seren-db" in source and "api_base" in source
    for i, line in enumerate(source.splitlines(), 1):
        if '"/projects' in line and marker in line:
            if base_has_publisher:
                continue  # base URL already includes publisher prefix
            assert "publishers/seren-db" in line, (
                f"{rel_path}:{i} hits /projects directly instead of "
                f"/publishers/seren-db/projects: {line.strip()}"
            )


@pytest.mark.parametrize("rel_path,marker", SERENDB_FILES, ids=IDS)
def test_all_project_paths_resolve_through_publisher(rel_path: str, marker: str) -> None:
    """Every /projects path in the file must ultimately resolve to
    /publishers/seren-db/projects/... at runtime."""
    source = _read_source(rel_path)
    has_publisher_in_base = False
    for line in source.splitlines():
        if "api_base" in line and "publishers/seren-db" in line and ("=" in line or "or" in line):
            has_publisher_in_base = True
            break

    for i, line in enumerate(source.splitlines(), 1):
        # Skip comments, docstrings, and test assertions
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or "assert" in stripped:
            continue
        if '"/projects' in line:
            ok = (
                "publishers/seren-db" in line  # path itself has prefix
                or has_publisher_in_base        # base URL has prefix
            )
            assert ok, (
                f"{rel_path}:{i} resolves to a bare /projects route: {stripped}"
            )


# -- polymarket/bot specific tests (has _execute_sql and branch lookup) --

def test_query_body_includes_project_and_branch() -> None:
    """The query POST body must include project_id and branch_id."""
    source = _read_source("polymarket/bot/scripts/serendb_storage.py")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_execute_sql":
            func_source = ast.get_source_segment(source, node)
            assert "project_id" in func_source
            assert "branch_id" in func_source
            break
    else:
        pytest.fail("_execute_sql function not found")


def test_branch_lookup_accepts_production() -> None:
    """Branch lookup must accept 'production' (Seren default) not just 'main'."""
    source = _read_source("polymarket/bot/scripts/serendb_storage.py")
    assert "production" in source
