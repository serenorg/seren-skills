"""Verify get_midpoint rejects wide-spread CLOB books that produce
meaningless ~0.50 midpoints.

Root cause of issue #265: books with bid=0.001, ask=0.999 yield
midpoint=0.50, which overwrites correct Gamma prices for low-
probability markets (e.g. ceasefire at 0.75% displayed as 50%).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_PATH = "polymarket/bot/scripts/polymarket_client.py"


def _source(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_get_midpoint_has_max_spread_guard() -> None:
    """get_midpoint must reject books where ask - bid > max_spread."""
    source = _source(CLIENT_PATH)
    assert "max_spread" in source, (
        "get_midpoint must accept a max_spread parameter"
    )
    # The guard must return 0.0 (falsy) so callers skip the midpoint
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "get_midpoint":
            body_source = ast.get_source_segment(source, node)
            assert "best_ask - best_bid > max_spread" in body_source, (
                "get_midpoint must check spread width before computing midpoint"
            )
            assert "return 0.0" in body_source, (
                "get_midpoint must return 0.0 for wide-spread books"
            )
            break
    else:
        pytest.fail("get_midpoint function not found in polymarket_client.py")


def test_agent_enrichment_skips_zero_midpoint() -> None:
    """agent.py must skip CLOB midpoint when get_midpoint returns 0.0.

    The existing guard `if live_mid and 0.01 < live_mid < 0.99` already
    rejects 0.0 (falsy).  Verify it is still present.
    """
    agent_src = _source("polymarket/bot/scripts/agent.py")
    assert "if live_mid and 0.01 < live_mid < 0.99" in agent_src, (
        "agent.py must gate CLOB midpoint overwrite on a truthy range check"
    )


def test_datetime_utcnow_removed() -> None:
    """agent.py must not use deprecated datetime.utcnow()."""
    agent_src = _source("polymarket/bot/scripts/agent.py")
    assert "utcnow()" not in agent_src, (
        "agent.py must use datetime.now(timezone.utc) instead of utcnow()"
    )
