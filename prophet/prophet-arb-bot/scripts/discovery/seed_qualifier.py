"""Seed-bet qualifier + trimmer (#542 Fix 2).

`run_auto_discover()` returns a `pending_ui_submission` list shaped for
Prophet's `/create` UI. Today that list is emitted verbatim — every
entry costs the operator `initial_bet_usdc` on Prophet at confirm-time,
and another `initial_bet_usdc` on Polymarket if the agent immediately
hedges (Fix 3 below). With a thin balance the operator approves Prophet
signing prompts that fail at confirm; with a deep Polymarket book on
one candidate and a paper-thin book on another, the hedge fails
post-fill and leaves naked exposure.

This module clamps both gaps:

  1. Drop candidates whose Polymarket book can't absorb a hedge of
     `initial_bet_usdc` at acceptable slippage (only in delta-neutral
     mode; single-leg passes `depth_assessor=None`).
  2. Rank surviving candidates by spread potential (Polymarket 24h
     volume is our proxy — pre-creation pairs have no Prophet odds).
  3. Trim to `max_fundable_count` (from `evaluate_seed_funds_preflight`).

Pure function. The agent supplies the depth callback so this module
doesn't need to know about `assess_polymarket_depth` or the hedger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class QualifierDecision:
    """Split of pending entries into kept vs dropped (with reasons)."""

    qualified: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)


# Reason codes — surfaced into the run envelope so the agent and the
# operator can see exactly why a candidate was dropped.
REASON_HEDGE_INELIGIBLE = "hedge_ineligible"
REASON_BANKROLL_TRIM = "bankroll_trim"


DepthAssessor = Callable[[str, float, float], bool]
"""Callback the agent supplies to the qualifier.

Args: (polymarket_market_id, size_usdc, max_slippage_bps).
Returns True when the visible Polymarket book can fund a hedge of
`size_usdc` at slippage ≤ `max_slippage_bps`.
"""


def _spread_potential_score(entry: dict) -> float:
    """Ranking proxy for pre-creation pairs.

    Polymarket markets with higher 24h volume have tighter spreads and
    deeper hedge depth, so when Prophet mirrors them the arb edge is
    more reliably capturable. We don't have Prophet odds yet (the
    market hasn't been created), so a Prophet-vs-Polymarket spread
    score is impossible here — volume is the best signal available
    pre-creation.
    """
    return float(entry.get("volume_24h_usd") or 0.0)


def qualify_and_trim_pending(
    *,
    pending: list[dict],
    max_fundable_count: int,
    initial_bet_usdc: float,
    depth_assessor: Optional[DepthAssessor],
    max_hedge_slippage_bps: float,
) -> QualifierDecision:
    """Filter + rank + trim pending UI entries.

    Order of operations matters: depth filtering runs BEFORE the bankroll
    cap so a hedge-ineligible candidate doesn't claim a slot the next
    fundable + hedgeable candidate could have taken. Then we rank by
    spread potential (descending) and slice to `max_fundable_count`.
    """
    if not pending:
        return QualifierDecision()

    # Phase 1 — depth eligibility (only when a depth_assessor is wired).
    eligible: list[dict] = []
    dropped: list[dict] = []
    for entry in pending:
        if depth_assessor is None:
            eligible.append(entry)
            continue
        market_id = str(entry.get("polymarket_market_id") or "")
        try:
            ok = bool(
                depth_assessor(market_id, float(initial_bet_usdc), float(max_hedge_slippage_bps))
            )
        except Exception:
            # A depth probe that raised is treated as ineligible — the
            # caller already logs the probe failure; we don't want to
            # silently include a candidate we couldn't qualify.
            ok = False
        if ok:
            eligible.append(entry)
        else:
            row = dict(entry)
            row["reason"] = REASON_HEDGE_INELIGIBLE
            dropped.append(row)

    # Phase 2 — rank by spread-potential proxy, then trim by bankroll.
    eligible.sort(key=_spread_potential_score, reverse=True)
    qualified = eligible[: max(0, int(max_fundable_count))]
    overflow = eligible[max(0, int(max_fundable_count)) :]
    for row in overflow:
        trimmed = dict(row)
        trimmed["reason"] = REASON_BANKROLL_TRIM
        dropped.append(trimmed)

    return QualifierDecision(qualified=qualified, dropped=dropped)
