"""Verify market selection sanity gates in polymarket/bot.

Tests fixes from issue #286:
1. Gamma API query params (end_date filters, sort_by passthrough)
2. Extreme-divergence sanity gate blocks absurd AI-vs-market gaps
3. Min buy price floor blocks penny-market BUY signals
4. Min volume floor rejects zero-volume seeded markets
5. Event-group dedup catches template-generated sibling markets
6. Gamma sort not trusted — client-side ranking does the real work
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


# --- Fix 1: Gamma query params ---


def test_get_markets_accepts_sort_and_date_params() -> None:
    """get_markets must accept sort_by, end_date_min, and end_date_max params."""
    source = _read(BOT_CLIENT)
    assert "sort_by" in source, "get_markets missing sort_by parameter"
    assert "end_date_min" in source, "get_markets missing end_date_min parameter"
    assert "end_date_max" in source, "get_markets missing end_date_max parameter"


def test_scan_markets_does_not_trust_gamma_sort() -> None:
    """scan_markets must NOT rely on Gamma's sort order (tested unreliable).
    It should pass sort_by='' or omit it, and let rank_candidates score."""
    source = _read(BOT_AGENT)
    # Should NOT have sort_by="volume" — Gamma's sort is broken
    assert not re.search(r'sort_by\s*=\s*["\']volume["\']', source), (
        "scan_markets still trusts Gamma sort_by='volume' — this is unreliable"
    )


def test_scan_markets_uses_server_side_date_filter() -> None:
    """scan_markets must pass end_date_min/max to avoid fetching expired markets."""
    source = _read(BOT_AGENT)
    assert "end_date_min" in source, "scan_markets does not pass end_date_min"
    assert "end_date_max" in source, "scan_markets does not pass end_date_max"


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
    assert float(match.group(1)) == 0.50


def test_max_divergence_in_config_example() -> None:
    config_text = _read(BOT_CONFIG)
    assert "max_divergence" in config_text


# --- Fix 3: Min buy price floor ---


def test_min_buy_price_gate_exists() -> None:
    source = _read(BOT_AGENT)
    assert "min_buy_price" in source


def test_min_buy_price_default_is_2pct() -> None:
    source = _read(BOT_AGENT)
    match = re.search(r"['\"]min_buy_price['\"],\s*([\d.]+)", source)
    assert match, "Cannot find min_buy_price default in agent.py"
    assert float(match.group(1)) == 0.02


def test_min_buy_price_in_config_example() -> None:
    config_text = _read(BOT_CONFIG)
    assert "min_buy_price" in config_text


# --- Fix 4: Min volume floor ---


def test_min_volume_filter_exists() -> None:
    """rank_candidates must reject markets below min_volume."""
    source = _read(BOT_AGENT)
    assert "min_volume" in source, "agent.py missing min_volume config"


def test_min_volume_default_is_5000() -> None:
    source = _read(BOT_AGENT)
    match = re.search(r"['\"]min_volume['\"],\s*([\d.]+)", source)
    assert match, "Cannot find min_volume default in agent.py"
    assert float(match.group(1)) == 5000.0


def test_min_volume_in_config_example() -> None:
    config_text = _read(BOT_CONFIG)
    assert "min_volume" in config_text


# --- Fix 5: Event-group dedup ---


def test_event_group_dedup_exists() -> None:
    """rank_candidates must deduplicate template-generated markets per event
    (e.g. 30 'Will X win the NBA Finals?' markets capped to 5)."""
    source = _read(BOT_AGENT)
    assert "MAX_PER_EVENT_GROUP" in source, "agent.py missing event-group dedup"
    assert "event_skips" in source, "agent.py missing event_skips counter"


def test_event_patterns_cover_major_sports() -> None:
    """Event patterns must match NBA, NHL, FIFA, F1, and election markets."""
    source = _read(BOT_AGENT)
    for keyword in ("nba", "nhl", "fifa", "f1", "republican", "democratic"):
        assert keyword in source.lower(), (
            f"Event-group patterns missing coverage for '{keyword}' markets"
        )


# --- Gate ordering ---


def test_gates_fire_before_kelly() -> None:
    """The divergence and min-price gates must appear before Kelly sizing."""
    source = _read(BOT_AGENT)
    div_pos = source.find("max_divergence")
    price_pos = source.find("min_buy_price")
    kelly_pos = source.find("calculate_position_size")
    assert div_pos > 0 and price_pos > 0 and kelly_pos > 0
    assert price_pos < kelly_pos, "min_buy_price gate must appear before Kelly"
    assert div_pos < kelly_pos, "max_divergence gate must appear before Kelly"


def test_volume_filter_before_slug_dedup() -> None:
    """min_volume filter must run before slug-group dedup to reduce the
    candidate set that dedup iterates over."""
    source = _read(BOT_AGENT)
    vol_pos = source.find("min_volume")
    slug_pos = source.find("MAX_PER_SLUG_GROUP")
    assert vol_pos > 0 and slug_pos > 0
    assert vol_pos < slug_pos, "min_volume filter must run before slug dedup"
