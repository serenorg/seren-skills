from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
STRATEGY_ENGINES = [
    "alpaca/saas-short-trader/scripts/strategy_engine.py",
    "alpaca/sass-short-trader-delta-neutral/scripts/strategy_engine.py",
]


def _fetch_market_features_source(rel_path: str) -> str:
    source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fetch_market_features":
            segment = ast.get_source_segment(source, node)
            assert segment is not None
            return segment
    raise AssertionError(f"fetch_market_features not found in {rel_path}")


@pytest.mark.parametrize("rel_path", STRATEGY_ENGINES, ids=STRATEGY_ENGINES)
def test_market_data_fetch_uses_snapshots_endpoint(rel_path: str) -> None:
    function_source = _fetch_market_features_source(rel_path)
    assert "/v2/stocks/snapshots" in function_source


@pytest.mark.parametrize("rel_path", STRATEGY_ENGINES, ids=STRATEGY_ENGINES)
def test_market_data_fetch_does_not_probe_clock_endpoint(rel_path: str) -> None:
    function_source = _fetch_market_features_source(rel_path)
    assert "/v2/clock" not in function_source
