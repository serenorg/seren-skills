"""Verify all polymarket skills guard against wide-spread fallback midpoints.

The fetch_midpoint callsites in polymarket_live.py compute a fallback_mid
from (best_bid + best_ask) / 2.  Without a spread guard, books with
bid=0.001 and ask=0.999 produce fallback_mid=0.50, poisoning downstream
price signals.  Each skill must cap the fallback at spread <= 0.50.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

AFFECTED_SKILLS = [
    "polymarket/bot/scripts/polymarket_live.py",
    "polymarket/maker-rebate-bot/scripts/polymarket_live.py",
    "polymarket/liquidity-paired-basis-maker/scripts/polymarket_live.py",
    "polymarket/high-throughput-paired-basis-maker/scripts/polymarket_live.py",
]


@pytest.mark.parametrize("rel_path", AFFECTED_SKILLS)
def test_fallback_mid_has_spread_guard(rel_path: str) -> None:
    """Each polymarket_live.py must guard fallback_mid with a spread check."""
    source = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert "spread <= 0.50" in source or "spread <= 0.5" in source, (
        f"{rel_path} must guard fallback_mid with a spread <= 0.50 check"
    )
    assert "else 0.0" in source, (
        f"{rel_path} must return 0.0 fallback when spread is too wide"
    )
