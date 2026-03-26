"""Verify market selection sanity gates in polymarket/bot.

Tests the three fixes from issue #286:
1. Gamma API query uses sort_by=volume and end_date filters
2. Extreme-divergence sanity gate blocks absurd AI-vs-market gaps
3. Min buy price floor blocks penny-market BUY signals
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BOT_AGENT = REPO_ROOT / "polymarket" / "bot" / "scripts" / "agent.py"
BOT_CLIENT = REPO_ROOT / "polymarket" / "bot" / "scripts" / "polymarket_client.py"
BOT_CONFIG = REPO_ROOT / "polymarket" / "bot" / "config.example.json"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- Fix 1: Smarter Gamma query ---


def test_get_markets_accepts_sort_and_date_params() -> None:
    """get_markets must accept sort_by, end_date_min, and end_date_max params."""
    source = _read(BOT_CLIENT)
    assert "sort_by" in source, "get_markets missing sort_by parameter"
    assert "end_date_min" in source, "get_markets missing end_date_min parameter"
    assert "end_date_max" in source, "get_markets missing end_date_max parameter"
    assert "order=" in source, "get_markets not passing order= to Gamma API"


def test_scan_markets_uses_volume_sort() -> None:
    """scan_markets must request volume-sorted results from Gamma."""
    source = _read(BOT_AGENT)
    # Find the scan_markets method and verify it passes sort_by="volume"
    assert re.search(r'sort_by\s*=\s*["\']volume["\']', source), (
        "scan_markets does not pass sort_by='volume' to get_markets"
    )


def test_scan_markets_uses_server_side_date_filter() -> None:
    """scan_markets must pass end_date_min/max to avoid fetching markets
    that will be discarded client-side."""
    source = _read(BOT_AGENT)
    assert "end_date_min=" in source or "end_date_min" in source, (
        "scan_markets does not pass end_date_min to get_markets"
    )
    assert "end_date_max=" in source or "end_date_max" in source, (
        "scan_markets does not pass end_date_max to get_markets"
    )


# --- Fix 2: Extreme-divergence sanity gate ---


def test_extreme_divergence_gate_exists() -> None:
    """evaluate_opportunity must block markets where AI-vs-market divergence
    exceeds max_divergence (default 50pp)."""
    source = _read(BOT_AGENT)
    assert "max_divergence" in source, "agent.py missing max_divergence config"
    assert "BLOCKED" in source and "divergence" in source.lower(), (
        "agent.py missing divergence sanity gate with BLOCKED log"
    )


def test_max_divergence_default_is_50pp() -> None:
    """max_divergence default must be 0.50 (50 percentage points)."""
    source = _read(BOT_AGENT)
    match = re.search(r"['\"]max_divergence['\"],\s*([\d.]+)", source)
    assert match, "Cannot find max_divergence default in agent.py"
    assert float(match.group(1)) == 0.50, (
        f"max_divergence default should be 0.50, got {match.group(1)}"
    )


def test_max_divergence_in_config_example() -> None:
    """config.example.json must include max_divergence."""
    config_text = _read(BOT_CONFIG)
    assert "max_divergence" in config_text, (
        "config.example.json missing max_divergence field"
    )


# --- Fix 3: Min buy price floor ---


def test_min_buy_price_gate_exists() -> None:
    """evaluate_opportunity must block BUY signals on markets priced below
    min_buy_price (default 2%)."""
    source = _read(BOT_AGENT)
    assert "min_buy_price" in source, "agent.py missing min_buy_price config"


def test_min_buy_price_default_is_2pct() -> None:
    """min_buy_price default must be 0.02 (2%)."""
    source = _read(BOT_AGENT)
    match = re.search(r"['\"]min_buy_price['\"],\s*([\d.]+)", source)
    assert match, "Cannot find min_buy_price default in agent.py"
    assert float(match.group(1)) == 0.02, (
        f"min_buy_price default should be 0.02, got {match.group(1)}"
    )


def test_min_buy_price_in_config_example() -> None:
    """config.example.json must include min_buy_price."""
    config_text = _read(BOT_CONFIG)
    assert "min_buy_price" in config_text, (
        "config.example.json missing min_buy_price field"
    )


# --- Gate ordering: both gates must fire before Kelly sizing ---


def test_gates_fire_before_kelly() -> None:
    """The divergence and min-price gates must appear before Kelly position
    sizing in evaluate_opportunity, so absurd values never reach the sizer."""
    source = _read(BOT_AGENT)
    div_pos = source.find("max_divergence")
    price_pos = source.find("min_buy_price")
    kelly_pos = source.find("calculate_position_size")
    assert div_pos > 0, "max_divergence not found in agent.py"
    assert price_pos > 0, "min_buy_price not found in agent.py"
    assert kelly_pos > 0, "calculate_position_size not found in agent.py"
    assert price_pos < kelly_pos, (
        "min_buy_price gate must appear before calculate_position_size"
    )
    assert div_pos < kelly_pos, (
        "max_divergence gate must appear before calculate_position_size"
    )
