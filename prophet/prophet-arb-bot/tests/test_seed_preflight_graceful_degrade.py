"""Issue #589 — seed preflight must not short-circuit existing pairs.

Bug: when `_apply_seed_preflight_and_trim` finds `max_fundable_count=0`
(operator can't fund a single seed bet), the previous behavior returned
a blocked `CycleResult` from inside the auto-discover branch, which
short-circuited `cmd_run` BEFORE the `for p in pairs:` scoring loop ran.

That means a configuration with 1 already-paired arb opportunity AND
50 pending_ui_submission candidates would block the cycle entirely —
even though the 1 paired opportunity requires zero seed funding and is
the actual arb the bot exists to trade.

Fix: when max_fundable=0, drop the pending_ui_submission list to empty
and continue scoring existing pairs. Only block when max_fundable=0 AND
no pairs exist (in which case there's literally nothing to do).
"""

from __future__ import annotations

from typing import Any

import pytest

from funds_preflight import SeedFundsPreflightResult


# ---------------------------------------------------------------------------
# The preflight helper now exposes a `trim_decision_when_unfundable` helper
# that returns the trimmed pending list + a status hint. The orchestration
# (cmd_run) uses the hint to decide block-vs-continue.


def test_zero_fundable_with_pairs_continues_and_trims_to_empty() -> None:
    """The critical regression: max_fundable=0 must not block when pairs
    exist. The runtime drops pending to [] and continues scoring."""
    from seed_preflight_orchestration import resolve_seed_preflight_action

    preflight = SeedFundsPreflightResult(
        ok=False,
        max_fundable_count=0,
        candidate_count=50,
        initial_bet_usdc=1.0,
        prophet_available_usdc=4.0,
        polymarket_available_usdc=0.0,
        prophet_deficit_usdc=46.0,
        polymarket_deficit_usdc=50.0,
    )

    action = resolve_seed_preflight_action(
        preflight=preflight,
        existing_pairs_count=1,
    )

    assert action.should_block is False
    assert action.trimmed_pending_ui_submission == []
    assert action.summary_blocker.startswith("seed_preflight_skipped:")
    assert "candidate_count=50" in action.summary_blocker


def test_zero_fundable_no_pairs_blocks_cycle() -> None:
    """When max_fundable=0 AND no existing pairs, the cycle has nothing
    to do — block with the deposit envelope so the operator funds up."""
    from seed_preflight_orchestration import resolve_seed_preflight_action

    preflight = SeedFundsPreflightResult(
        ok=False,
        max_fundable_count=0,
        candidate_count=50,
        initial_bet_usdc=1.0,
        prophet_available_usdc=0.0,
        polymarket_available_usdc=0.0,
        prophet_deficit_usdc=50.0,
        polymarket_deficit_usdc=50.0,
    )

    action = resolve_seed_preflight_action(
        preflight=preflight,
        existing_pairs_count=0,
    )

    assert action.should_block is True
    assert action.block_reason == "funds_insufficient_for_seeds"
    assert action.deposit_envelope["max_fundable_count"] == 0


def test_partial_fundability_proceeds_normally() -> None:
    """The unchanged happy path: max_fundable>0 means the qualifier
    handles the trim and the orchestration just continues."""
    from seed_preflight_orchestration import resolve_seed_preflight_action

    preflight = SeedFundsPreflightResult(
        ok=True,
        max_fundable_count=3,
        candidate_count=10,
        initial_bet_usdc=1.0,
        prophet_available_usdc=5.0,
        polymarket_available_usdc=3.0,
        prophet_deficit_usdc=5.0,
        polymarket_deficit_usdc=7.0,
    )

    action = resolve_seed_preflight_action(
        preflight=preflight,
        existing_pairs_count=0,
    )

    assert action.should_block is False
    # When ok=True, the qualifier (called separately) does the trimming.
    # The orchestration helper has nothing to do here.
    assert action.trimmed_pending_ui_submission is None
