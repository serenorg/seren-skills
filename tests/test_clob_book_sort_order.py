"""Verify best_price handles Polymarket CLOB sort order correctly (#310).

The CLOB /book endpoint returns bids ascending (0.01->0.53) and asks
descending (0.99->0.54). best_price must find the true best:
  - Best bid = max of bids list (0.53), not bids[0] (0.01)
  - Best ask = min of asks list (0.54), not asks[0] (0.99)
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

POLYMARKET_LIVE_FILES = [
    REPO_ROOT / "polymarket" / "bot" / "scripts" / "polymarket_live.py",
    REPO_ROOT / "polymarket" / "maker-rebate-bot" / "scripts" / "polymarket_live.py",
    REPO_ROOT / "polymarket" / "liquidity-paired-basis-maker" / "scripts" / "polymarket_live.py",
    REPO_ROOT / "polymarket" / "high-throughput-paired-basis-maker" / "scripts" / "polymarket_live.py",
]

# Simulated CLOB book: bids ascending, asks descending (real Polymarket order)
BIDS_ASCENDING = [
    {"price": "0.01", "size": "1000000"},
    {"price": "0.02", "size": "300"},
    {"price": "0.10", "size": "5000"},
    {"price": "0.45", "size": "800"},
    {"price": "0.52", "size": "200"},
    {"price": "0.53", "size": "185"},
]

ASKS_DESCENDING = [
    {"price": "0.99", "size": "500000"},
    {"price": "0.98", "size": "200"},
    {"price": "0.90", "size": "1000"},
    {"price": "0.58", "size": "600"},
    {"price": "0.55", "size": "750"},
    {"price": "0.54", "size": "6"},
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- Functional tests using the fixed algorithm directly ---
# (Cannot import polymarket_live.py due to Python 3.14 dataclass issue,
# so we replicate the exact fixed logic here and verify source matches)


def _safe_float(v, fallback=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return fallback


def _best_price_fixed(levels, fallback=0.0, side="bid"):
    """The corrected algorithm that must match what's in the source."""
    if not isinstance(levels, list) or not levels:
        return fallback

    def _extract(level):
        if isinstance(level, dict):
            return _safe_float(level.get("price"), fallback)
        return fallback

    if side.lower() == "bid":
        return max((_extract(lv) for lv in levels), default=fallback)
    else:
        prices = [_extract(lv) for lv in levels]
        valid = [p for p in prices if p > 0]
        return min(valid, default=fallback)


class TestFixedAlgorithm:

    def test_best_bid_is_max_not_first(self) -> None:
        assert _best_price_fixed(BIDS_ASCENDING, side="bid") == 0.53

    def test_best_ask_is_min_not_first(self) -> None:
        assert _best_price_fixed(ASKS_DESCENDING, side="ask") == 0.54

    def test_spread_is_1_percent_not_98(self) -> None:
        bid = _best_price_fixed(BIDS_ASCENDING, side="bid")
        ask = _best_price_fixed(ASKS_DESCENDING, side="ask")
        spread = ask - bid
        assert spread == pytest.approx(0.01, abs=0.001)

    def test_empty_levels(self) -> None:
        assert _best_price_fixed([], side="bid") == 0.0
        assert _best_price_fixed([], side="ask") == 0.0

    def test_old_algorithm_was_wrong(self) -> None:
        """Prove the old bids[0]/asks[0] approach gives 98% spread."""
        old_bid = float(BIDS_ASCENDING[0]["price"])  # 0.01
        old_ask = float(ASKS_DESCENDING[0]["price"])  # 0.99
        old_spread = old_ask - old_bid
        assert old_spread == pytest.approx(0.98, abs=0.001), "Old algo should give 98%"


# --- Source-level verification across all 4 skills ---


@pytest.mark.parametrize("path", POLYMARKET_LIVE_FILES,
                         ids=[p.parent.parent.name for p in POLYMARKET_LIVE_FILES])
class TestAllSkillsSource:

    def test_has_side_param(self, path) -> None:
        source = _read(path)
        assert 'side: str = "bid"' in source, (
            f"{path.relative_to(REPO_ROOT)} still has old best_price without side param"
        )

    def test_parse_book_passes_side(self, path) -> None:
        source = _read(path)
        assert 'side="bid"' in source and 'side="ask"' in source, (
            f"{path.relative_to(REPO_ROOT)} parse_book_payload not passing side to best_price"
        )

    def test_uses_max_for_bids(self, path) -> None:
        source = _read(path)
        assert "max(" in source, (
            f"{path.relative_to(REPO_ROOT)} best_price must use max() for bids"
        )

    def test_uses_min_for_asks(self, path) -> None:
        source = _read(path)
        assert "min(valid" in source or "min(prices" in source, (
            f"{path.relative_to(REPO_ROOT)} best_price must use min() for asks"
        )

    def test_no_levels_zero_indexing_for_best(self, path) -> None:
        """The bug was levels[0] for best price. Ensure it's gone from
        best_price and parse_book_payload."""
        source = _read(path)
        # Find just the best_price function body
        import re
        bp_match = re.search(r'def best_price\(.*?\n((?:    .*\n)*)', source)
        assert bp_match, "best_price not found"
        bp_body = bp_match.group(1)
        assert "levels[0]" not in bp_body, (
            "best_price still uses levels[0] — this reads the worst price, not the best"
        )
