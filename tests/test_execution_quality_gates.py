"""Verify execution quality gates in polymarket/bot (issue #308).

1. Edge-to-spread ratio gate — rejects trades where spread eats the edge
2. Depth-constrained position sizing — caps positions at fraction of visible book
3. get_book_metrics — returns spread and depth from CLOB
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BOT_AGENT = REPO_ROOT / "polymarket" / "bot" / "scripts" / "agent.py"
BOT_CLIENT = REPO_ROOT / "polymarket" / "bot" / "scripts" / "polymarket_client.py"
BOT_CONFIG = REPO_ROOT / "polymarket" / "bot" / "config.example.json"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- Fix 1: Edge-to-spread ratio gate ---


def test_edge_to_spread_gate_exists() -> None:
    source = _read(BOT_AGENT)
    assert "edge_to_spread" in source, "agent.py missing edge-to-spread ratio computation"
    assert "min_edge_to_spread_ratio" in source, "agent.py missing min_edge_to_spread_ratio config"
    assert "BLOCKED" in source and "spread eats" in source.lower(), (
        "agent.py missing BLOCKED log for edge-to-spread gate"
    )


def test_edge_to_spread_default_is_3() -> None:
    source = _read(BOT_AGENT)
    match = re.search(r"['\"]min_edge_to_spread_ratio['\"],\s*([\d.]+)", source)
    assert match, "Cannot find min_edge_to_spread_ratio default"
    assert float(match.group(1)) == 3.0


def test_edge_to_spread_in_config() -> None:
    config = _read(BOT_CONFIG)
    assert "min_edge_to_spread_ratio" in config


def test_edge_to_spread_fires_before_kelly() -> None:
    source = _read(BOT_AGENT)
    gate_pos = source.find("edge_to_spread")
    kelly_pos = source.find("calculate_position_size")
    assert gate_pos > 0 and kelly_pos > 0
    assert gate_pos < kelly_pos, "edge-to-spread gate must fire before Kelly sizing"


# --- Fix 2: Depth-constrained position sizing ---


def test_depth_constraint_exists() -> None:
    source = _read(BOT_AGENT)
    assert "max_depth_fraction" in source, "agent.py missing max_depth_fraction config"
    assert "depth cap" in source.lower() or "depth_cap" in source.lower() or "max_from_depth" in source, (
        "agent.py missing depth-constrained sizing logic"
    )


def test_depth_fraction_default_is_025() -> None:
    source = _read(BOT_AGENT)
    match = re.search(r"['\"]max_depth_fraction['\"],\s*([\d.]+)", source)
    assert match, "Cannot find max_depth_fraction default"
    assert float(match.group(1)) == 0.25


def test_depth_constraint_in_config() -> None:
    config = _read(BOT_CONFIG)
    assert "max_depth_fraction" in config


def test_depth_constraint_after_kelly_before_ev() -> None:
    """Depth cap must apply after Kelly computes size but before EV calculation."""
    source = _read(BOT_AGENT)
    kelly_pos = source.find("calculate_position_size")
    depth_pos = source.find("max_from_depth")
    ev_pos = source.find("calculate_expected_value")
    assert kelly_pos > 0 and depth_pos > 0 and ev_pos > 0
    assert kelly_pos < depth_pos < ev_pos, (
        "Depth cap must sit between Kelly sizing and EV calculation"
    )


# --- get_book_metrics ---


def test_client_has_get_book_metrics() -> None:
    source = _read(BOT_CLIENT)
    assert "def get_book_metrics" in source
    assert "spread" in source
    assert "bid_depth_usd" in source
    assert "ask_depth_usd" in source


# --- Integration: agent calls get_book_metrics ---


def test_agent_calls_book_metrics() -> None:
    source = _read(BOT_AGENT)
    assert "get_book_metrics" in source, "agent.py must call get_book_metrics for spread/depth"
