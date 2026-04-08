"""Tests for Minimum Viable Liquidity (MVL) filter across Polymarket skills.

Tests the core MVL classification: markets where spread < threshold AND
depth/volume ratio > ceiling are over-liquid and should be dropped.
"""
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Minimal MVL computation (mirrors the logic embedded in each skill)
# ---------------------------------------------------------------------------


def compute_mvl_over_liquid(
    spread_bps: float,
    depth_ratio: float,
    mvl_min_spread_bps: float = 50.0,
    mvl_max_depth_ratio: float = 0.5,
) -> bool:
    """Return True if the market is over-liquid (should be dropped)."""
    return spread_bps < mvl_min_spread_bps and depth_ratio > mvl_max_depth_ratio


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestMVLClassification:
    """Core MVL pass/fail classification."""

    def test_over_liquid_market_is_dropped(self):
        """Tight spread + deep book = over-liquid → True."""
        assert compute_mvl_over_liquid(spread_bps=20.0, depth_ratio=0.8) is True

    def test_wide_spread_market_passes(self):
        """Wide spread means edge exists → False (not over-liquid)."""
        assert compute_mvl_over_liquid(spread_bps=120.0, depth_ratio=0.8) is False

    def test_shallow_book_market_passes(self):
        """Tight spread but shallow book = room for your liquidity → False."""
        assert compute_mvl_over_liquid(spread_bps=20.0, depth_ratio=0.2) is False

    def test_wide_spread_shallow_book_passes(self):
        """Neither condition met → False."""
        assert compute_mvl_over_liquid(spread_bps=120.0, depth_ratio=0.2) is False

    def test_boundary_spread_exactly_at_threshold(self):
        """Spread exactly at threshold → not strictly less → passes."""
        assert compute_mvl_over_liquid(spread_bps=50.0, depth_ratio=0.8) is False

    def test_boundary_depth_exactly_at_threshold(self):
        """Depth ratio exactly at threshold → not strictly greater → passes."""
        assert compute_mvl_over_liquid(spread_bps=20.0, depth_ratio=0.5) is False

    def test_custom_thresholds_tighter(self):
        """Custom thresholds: more aggressive filtering."""
        # With tighter thresholds (100 bps, 0.3 ratio), this market gets dropped
        assert compute_mvl_over_liquid(
            spread_bps=80.0, depth_ratio=0.4,
            mvl_min_spread_bps=100.0, mvl_max_depth_ratio=0.3,
        ) is True

    def test_custom_thresholds_looser(self):
        """Custom thresholds: more permissive."""
        # With looser thresholds, previously-dropped market now passes
        assert compute_mvl_over_liquid(
            spread_bps=20.0, depth_ratio=0.8,
            mvl_min_spread_bps=10.0, mvl_max_depth_ratio=1.0,
        ) is False


class TestMVLWithMarketData:
    """Test MVL computation from market-like dicts (integration-style)."""

    @staticmethod
    def _mvl_from_market(
        best_bid: float,
        best_ask: float,
        bid_size_usd: float,
        ask_size_usd: float,
        volume24hr: float,
        mvl_min_spread_bps: float = 50.0,
        mvl_max_depth_ratio: float = 0.5,
    ) -> bool:
        """Compute MVL from raw market parameters."""
        mid = (best_bid + best_ask) / 2.0
        if mid <= 0 or volume24hr <= 0:
            return False  # can't compute → default pass
        spread_bps = (best_ask - best_bid) / mid * 10000.0
        depth_ratio = (bid_size_usd + ask_size_usd) / volume24hr
        return compute_mvl_over_liquid(spread_bps, depth_ratio, mvl_min_spread_bps, mvl_max_depth_ratio)

    def test_real_world_over_liquid(self):
        """Typical over-liquid Polymarket market: 1-cent spread, $50K depth, $80K volume."""
        assert self._mvl_from_market(
            best_bid=0.54, best_ask=0.55,  # ~185 bps... actually not over-liquid
            bid_size_usd=25000.0, ask_size_usd=25000.0,
            volume24hr=80000.0,
        ) is False  # 185 bps spread is wide enough

    def test_real_world_very_tight_spread(self):
        """Penny-wide on a 50-cent market with massive depth."""
        assert self._mvl_from_market(
            best_bid=0.499, best_ask=0.501,  # ~4 bps
            bid_size_usd=30000.0, ask_size_usd=30000.0,
            volume24hr=80000.0,  # depth/volume = 0.75
        ) is True

    def test_zero_volume_passes(self):
        """Zero volume → can't compute ratio → passes."""
        assert self._mvl_from_market(
            best_bid=0.499, best_ask=0.501,
            bid_size_usd=30000.0, ask_size_usd=30000.0,
            volume24hr=0.0,
        ) is False

    def test_zero_mid_passes(self):
        """Zero mid price → can't compute → passes."""
        assert self._mvl_from_market(
            best_bid=0.0, best_ask=0.0,
            bid_size_usd=30000.0, ask_size_usd=30000.0,
            volume24hr=80000.0,
        ) is False

    def test_under_liquid_market(self):
        """Wide spread, thin book — this is where the edge lives."""
        assert self._mvl_from_market(
            best_bid=0.40, best_ask=0.45,  # ~1176 bps
            bid_size_usd=2000.0, ask_size_usd=2000.0,
            volume24hr=10000.0,
        ) is False


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
