"""Orchestration helper that decides what to do with a seed preflight result.

Issue #589: when `max_fundable_count == 0`, the old code returned a
blocked `CycleResult` from inside the auto-discover branch, which
short-circuited `cmd_run` BEFORE the scoring loop could trade existing
paired arb opportunities. That meant a configuration with 1 existing
pair + 50 unfundable pending candidates would block the cycle even
though the 1 existing pair requires zero seed funding.

This helper inverts the decision: it returns a small `Action` record
that tells `cmd_run` what to do, and `cmd_run` itself decides whether
to short-circuit. Two outcomes:

  1. `max_fundable == 0` AND `existing_pairs == 0` — nothing to do.
     Block with deposit envelope. `should_block=True`.

  2. `max_fundable == 0` AND `existing_pairs > 0` — drop pending, keep
     trading the existing pairs. `should_block=False` and
     `trimmed_pending_ui_submission == []`.

The qualifier (`qualify_and_trim_pending`) still handles the
positive-fundable case; this helper is the orchestration glue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from funds_preflight import SeedFundsPreflightResult


@dataclass
class SeedPreflightAction:
    """What `cmd_run` should do with a `SeedFundsPreflightResult`.

    `trimmed_pending_ui_submission == None` means the orchestration has
    no opinion — fall through to the qualifier. `[]` means the
    orchestration explicitly emptied the list (because no seeds are
    fundable but pairs exist, so the run should continue).
    """

    should_block: bool
    trimmed_pending_ui_submission: Optional[list[dict]]
    summary_blocker: Optional[str]
    block_reason: Optional[str]
    deposit_envelope: Optional[dict]


def resolve_seed_preflight_action(
    *,
    preflight: SeedFundsPreflightResult,
    existing_pairs_count: int,
) -> SeedPreflightAction:
    """Decide block-vs-continue for a seed preflight outcome.

    `existing_pairs_count` is the number of rows in `arb_pairs` BEFORE
    auto-discover runs (so we don't conflate "1 pre-existing pair the
    bot has been trading for weeks" with "1 freshly-paired row that
    needs a seed bet to materialize on Prophet").

    Returns:
        SeedPreflightAction describing the next step for `cmd_run`.
    """
    if preflight.ok:
        # Positive fundable count — the qualifier will trim the pending
        # list to `max_fundable_count`. Nothing for the orchestration
        # to do.
        return SeedPreflightAction(
            should_block=False,
            trimmed_pending_ui_submission=None,
            summary_blocker=None,
            block_reason=None,
            deposit_envelope=None,
        )

    # max_fundable_count == 0 from here on.
    if existing_pairs_count <= 0:
        # Nothing fundable AND nothing already paired. Block — there's
        # genuinely nothing the cycle can do until the operator funds.
        return SeedPreflightAction(
            should_block=True,
            trimmed_pending_ui_submission=None,
            summary_blocker=(
                "funds_insufficient_for_seeds:"
                f"prophet_deficit={preflight.prophet_deficit_usdc}_usdc;"
                f"polymarket_deficit={preflight.polymarket_deficit_usdc}_usdc"
            ),
            block_reason="funds_insufficient_for_seeds",
            deposit_envelope=preflight.to_deposit_envelope(),
        )

    # max_fundable_count == 0 AND we have existing pairs to score.
    # Drop the pending list and continue — the operator can still see
    # the deposit gap in the summary, but the bot trades the pairs it
    # already has.
    return SeedPreflightAction(
        should_block=False,
        trimmed_pending_ui_submission=[],
        summary_blocker=(
            "seed_preflight_skipped:"
            f"candidate_count={preflight.candidate_count}:"
            f"prophet_deficit={preflight.prophet_deficit_usdc}_usdc;"
            f"polymarket_deficit={preflight.polymarket_deficit_usdc}_usdc"
        ),
        block_reason=None,
        deposit_envelope=None,
    )
