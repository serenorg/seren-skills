"""Seed-bet preflight + trim-to-bankroll (#542 Fix 2).

Today every entry in `pending_ui_submission` will cost the operator
`initial_bet_usdc` on Prophet at confirm-time, and (in delta-neutral
mode) `initial_bet_usdc` on Polymarket for the hedge. Emitting 12
entries when the Prophet Safe has $5 means 7 Privy prompts will be
approved that subsequently fail at confirm — wasted clicks and a worse
operator experience than the unmatched-funds block.

The seed preflight + trimmer fixes both halves:

  1. `evaluate_seed_funds_preflight` decides how many entries the
     operator can actually fund right now, and returns a split-deficit
     envelope when the answer is zero.
  2. `qualify_and_trim_pending` filters `pending_ui_submission` by
     fund availability + Polymarket hedge depth, ranks by spread
     potential (proxy: 24h volume since pre-creation pairs have no
     Prophet odds yet), and returns the kept + dropped lists.

Both helpers are pure — they take balances and a depth callback and
return plain dicts. The agent wires them in around the existing
`run_auto_discover()` return.
"""

from __future__ import annotations

from typing import Any

import pytest

from funds_preflight import evaluate_seed_funds_preflight
from discovery.seed_qualifier import (
    QualifierDecision,
    qualify_and_trim_pending,
)


def _pending(
    polymarket_market_id: str,
    *,
    volume_24h: float,
    initial_bet_usdc: float = 1.0,
    polymarket_yes_token_id: str | None = None,
) -> dict:
    """Shape mirrors `_build_pending_entry` in auto_discover.py."""
    return {
        "polymarket_market_id": polymarket_market_id,
        # #631: distinct default token_id so tests that don't override
        # can still tell which id the qualifier passed downstream.
        "polymarket_yes_token_id": (
            polymarket_yes_token_id
            if polymarket_yes_token_id is not None
            else f"TOKEN_{polymarket_market_id}"
        ),
        "question": f"q for {polymarket_market_id}",
        "category": "Sports",
        "category_slug": "sports",
        "resolution_date_iso": "2026-05-20T22:00:00Z",
        "initial_bet_usdc": initial_bet_usdc,
        "bounty_id": "",
        "prophet_viewer_id": "",
        "source_skill": "prophet-arb-bot",
        # The qualifier uses this for ranking — Polymarket 24h volume
        # is our proxy for "spread potential" pre-creation.
        "volume_24h_usd": volume_24h,
    }


# ---------------------------------------------------------------------------
# evaluate_seed_funds_preflight


def test_seed_preflight_returns_zero_when_both_venues_empty() -> None:
    result = evaluate_seed_funds_preflight(
        candidate_count=12,
        initial_bet_usdc=1.0,
        prophet_available_usdc=0.0,
        polymarket_available_usdc=0.0,
    )
    assert result.ok is False
    assert result.max_fundable_count == 0
    assert result.prophet_deficit_usdc == 12.0
    assert result.polymarket_deficit_usdc == 12.0
    envelope = result.to_deposit_envelope()
    assert envelope["prophet_deficit_usdc"] == 12.0
    assert envelope["polymarket_deficit_usdc"] == 12.0
    assert envelope["chain"] == "polygon"
    assert envelope["chain_id"] == 137


def test_seed_preflight_caps_by_thinnest_venue() -> None:
    """Two-venue: max_fundable = min(prophet_floor, polymarket_floor).

    Prophet has $5, Polymarket has $3 — operator can fund 3 seeds
    total this run. Deficits report the gap to FULL fundability
    (all 12 candidates × $1 = $12 per venue), so the deposit runbook
    can tell the operator how much to top up to clear the trim.
    """
    result = evaluate_seed_funds_preflight(
        candidate_count=12,
        initial_bet_usdc=1.0,
        prophet_available_usdc=5.0,
        polymarket_available_usdc=3.0,
    )
    assert result.ok is True
    assert result.max_fundable_count == 3
    # Deficits = full_needed (12) - available_per_venue.
    assert result.prophet_deficit_usdc == 7.0
    assert result.polymarket_deficit_usdc == 9.0


def test_seed_preflight_uses_bet_size() -> None:
    """A higher initial_bet_usdc reduces the count proportionally."""
    result = evaluate_seed_funds_preflight(
        candidate_count=12,
        initial_bet_usdc=2.0,
        prophet_available_usdc=5.0,
        polymarket_available_usdc=5.0,
    )
    assert result.max_fundable_count == 2  # floor(5 / 2) on both sides


def test_seed_preflight_caps_at_candidate_count() -> None:
    """If the operator has tons of money but only 3 candidates, we
    don't claim we can fund 100 seeds."""
    result = evaluate_seed_funds_preflight(
        candidate_count=3,
        initial_bet_usdc=1.0,
        prophet_available_usdc=1000.0,
        polymarket_available_usdc=1000.0,
    )
    assert result.max_fundable_count == 3


def test_seed_preflight_rejects_nonpositive_bet() -> None:
    """A $0 seed would imply infinite candidates — reject the
    config rather than silently allow it."""
    with pytest.raises(ValueError):
        evaluate_seed_funds_preflight(
            candidate_count=5,
            initial_bet_usdc=0.0,
            prophet_available_usdc=10.0,
            polymarket_available_usdc=10.0,
        )


# ---------------------------------------------------------------------------
# qualify_and_trim_pending


def test_qualifier_trims_to_max_fundable_ranked_by_volume() -> None:
    """Three candidates, max_fundable=2. The two with the highest
    Polymarket 24h volume win (spread-potential proxy)."""
    pending = [
        _pending("LOW", volume_24h=10_000),
        _pending("HIGH", volume_24h=80_000),
        _pending("MID", volume_24h=40_000),
    ]
    decision = qualify_and_trim_pending(
        pending=pending,
        max_fundable_count=2,
        initial_bet_usdc=1.0,
        depth_assessor=None,
        max_hedge_slippage_bps=200.0,
    )
    assert [e["polymarket_market_id"] for e in decision.qualified] == ["HIGH", "MID"]
    assert [d["polymarket_market_id"] for d in decision.dropped] == ["LOW"]
    assert decision.dropped[0]["reason"] == "bankroll_trim"


def test_qualifier_drops_depth_insufficient_before_bankroll_trim() -> None:
    """A candidate whose Polymarket book can't cover `initial_bet_usdc`
    at acceptable slippage must NOT count against the bankroll cap.

    The qualifier should drop it, log `reason='hedge_ineligible'`, and
    fill the slot with the next-ranked candidate that does pass depth.
    """
    pending = [
        _pending("A", volume_24h=80_000),
        _pending("B-NO-DEPTH", volume_24h=70_000),
        _pending("C", volume_24h=60_000),
    ]

    def fake_depth(token_id: str, size_usdc: float, max_slippage_bps: float) -> bool:
        # #631: the qualifier feeds the YES token_id here, not the
        # condition_id. The _pending helper defaults
        # polymarket_yes_token_id to f"TOKEN_{polymarket_market_id}".
        return token_id != "TOKEN_B-NO-DEPTH"

    decision = qualify_and_trim_pending(
        pending=pending,
        max_fundable_count=2,
        initial_bet_usdc=1.0,
        depth_assessor=fake_depth,
        max_hedge_slippage_bps=200.0,
    )
    assert [e["polymarket_market_id"] for e in decision.qualified] == ["A", "C"]
    dropped_by_id = {d["polymarket_market_id"]: d["reason"] for d in decision.dropped}
    assert dropped_by_id == {"B-NO-DEPTH": "hedge_ineligible"}


def test_qualifier_returns_empty_when_max_fundable_zero() -> None:
    pending = [_pending("X", volume_24h=999_999)]
    decision = qualify_and_trim_pending(
        pending=pending,
        max_fundable_count=0,
        initial_bet_usdc=1.0,
        depth_assessor=None,
        max_hedge_slippage_bps=200.0,
    )
    assert decision.qualified == []
    assert [d["polymarket_market_id"] for d in decision.dropped] == ["X"]
    assert decision.dropped[0]["reason"] == "bankroll_trim"


def test_qualifier_skips_depth_when_assessor_none() -> None:
    """Dry-run cycles (no live hedger) pass `depth_assessor=None` —
    no hedge eligibility check is possible without the trader handle.
    The qualifier should still trim by bankroll. (Pre-#591 this branch
    also served `single_leg` mode; with single_leg removed the dry-run
    path is the sole reason `depth_assessor` is ever None.)"""
    pending = [
        _pending("A", volume_24h=10_000),
        _pending("B", volume_24h=20_000),
    ]
    decision = qualify_and_trim_pending(
        pending=pending,
        max_fundable_count=5,
        initial_bet_usdc=1.0,
        depth_assessor=None,
        max_hedge_slippage_bps=200.0,
    )
    assert len(decision.qualified) == 2
    assert decision.dropped == []


def test_qualifier_decision_dataclass_shape() -> None:
    decision = qualify_and_trim_pending(
        pending=[],
        max_fundable_count=0,
        initial_bet_usdc=1.0,
        depth_assessor=None,
        max_hedge_slippage_bps=200.0,
    )
    assert isinstance(decision, QualifierDecision)
    assert decision.qualified == []
    assert decision.dropped == []


def test_qualifier_feeds_token_id_to_depth_assessor_not_condition_id() -> None:
    """Issue #631 — the bug closure assertion.

    The qualifier MUST pass `polymarket_yes_token_id` to the depth
    assessor. Pre-fix it passed `polymarket_market_id` (a hex condition
    id), which made every Polymarket CLOB `/book?token_id=...` probe
    return an empty payload — every auto-discover candidate was marked
    `hedge_ineligible` regardless of real book depth.

    The fixture sets `polymarket_market_id` and
    `polymarket_yes_token_id` to distinct values so the test cannot
    pass by coincidence: only the token_id selection unblocks the
    candidate.
    """
    received_ids: list[str] = []

    def capture_depth(market_id: str, size_usdc: float, max_slippage_bps: float) -> bool:
        received_ids.append(market_id)
        return True  # accept everything — we're testing what gets passed in.

    pending = [
        _pending(
            "cond_abc",
            volume_24h=50_000,
            polymarket_yes_token_id="1111111111111111111",
        ),
        _pending(
            "cond_def",
            volume_24h=40_000,
            polymarket_yes_token_id="2222222222222222222",
        ),
    ]

    decision = qualify_and_trim_pending(
        pending=pending,
        max_fundable_count=5,
        initial_bet_usdc=1.0,
        depth_assessor=capture_depth,
        max_hedge_slippage_bps=200.0,
    )

    # The depth assessor saw the YES token_ids — the values Polymarket
    # CLOB's `/book` endpoint requires. It did NOT see the hex
    # condition_ids that would have triggered the original bug.
    assert received_ids == [
        "1111111111111111111",
        "2222222222222222222",
    ]
    assert [e["polymarket_market_id"] for e in decision.qualified] == [
        "cond_abc",
        "cond_def",
    ]
