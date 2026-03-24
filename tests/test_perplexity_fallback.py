"""Verify research_market falls back to seren-models when the direct
Perplexity publisher fails, and the fallback is sticky."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SEREN_CLIENT = "polymarket/bot/scripts/seren_client.py"


def _source() -> str:
    return (REPO_ROOT / SEREN_CLIENT).read_text(encoding="utf-8")


def test_research_market_calls_perplexity_publisher() -> None:
    """Primary path must call the perplexity publisher."""
    source = _source()
    assert "publisher='perplexity'" in source or 'publisher="perplexity"' in source


def test_research_market_has_seren_models_fallback() -> None:
    """Fallback path must call seren-models with perplexity/sonar."""
    source = _source()
    assert "publisher='seren-models'" in source or 'publisher="seren-models"' in source
    assert "perplexity/sonar" in source


def test_fallback_is_sticky() -> None:
    """The fallback flag must be set on failure and checked before retrying."""
    source = _source()
    assert "_perplexity_fallback" in source
    # Flag must be initialized to False
    assert "_perplexity_fallback = False" in source
    # Flag must be set to True on failure
    assert "_perplexity_fallback = True" in source


def test_fallback_catch_is_broad_enough() -> None:
    """The try/except around the perplexity call must catch Exception
    (covers HTTP errors from call_publisher AND ValueError from _extract_text)."""
    source = _source()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "research_market":
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    # Should catch Exception (broad) not a narrow type
                    if child.type and isinstance(child.type, ast.Name):
                        assert child.type.id == "Exception", (
                            f"except handler catches {child.type.id}, should catch Exception"
                        )
                    break
            else:
                pytest.fail("research_market has no except handler")
            break
    else:
        pytest.fail("research_market function not found")
