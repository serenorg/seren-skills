"""Delta-neutral hedge module (#536).

The arb-bot's default `Mode A` quotes passive LIMITs on Prophet and uses
Polymarket as a fair-value reference only — the polymarket leg is **not**
a hedge. Issue #536 adds an opt-in `execution_mode="delta_neutral"` that
submits the offsetting Polymarket order after a Prophet fill, making the
"delta neutral" claim honest.

Critical paths under test:

  1. **Pre-trade depth check.** Before posting a Prophet limit we sweep
     the Polymarket book. If visible bid/ask depth at acceptable slippage
     can't cover the planned Prophet notional, the opportunity is rejected
     this cycle — preventing the "Prophet fills, Polymarket can't hedge"
     failure mode at the source.

  2. **Post-fill hedge submission.** When `list_user_orders` reports a
     previously-open Prophet order has filled, the hedger immediately
     submits the offsetting Polymarket order. The hedge order id and
     fill metadata are returned so the recorder can persist them.

  3. **Hedge failure → Prophet unwind.** If Polymarket submission fails
     (book moved, CLOB rejection, missing depth), the hedger invokes
     the unwind callback. Operators get a structured naked-exposure
     blocker rather than a silent half-position.

These three behaviors are the entire delta-neutral surface. We do **not**
test mid-price math, Kelly sizing, or Prophet GraphQL parsing here —
those have their own focused suites.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from arbitrage.hedge import (
    DepthAssessment,
    HedgeOutcome,
    assess_polymarket_depth,
    hedge_filled_order,
)


# ---------------------------------------------------------------------------
# Book fixtures — small enough that a human can verify by hand.


def _book(*, bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> dict:
    """Build a Polymarket-style book payload.

    Real polymarket-data responses surface depth under `raw.bids` /
    `raw.asks` as list-of-dict with `price` and `size`. We mirror that
    shape so the production `fetch_book` and the test fixture share
    one parser code path.
    """
    return {
        "raw": {
            "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
            "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
        },
        "best_bid": bids[0][0] if bids else 0.0,
        "best_ask": asks[0][0] if asks else 0.0,
        "tick_size": "0.01",
    }


# ---------------------------------------------------------------------------
# 1. Pre-trade depth check


def test_depth_check_rejects_thin_book_for_hedge_size() -> None:
    """If the visible Polymarket book can't fill the planned Prophet
    notional, the opportunity is rejected this cycle.

    Without this guard the bot would post a Prophet limit, the limit
    would fill, and the hedge would fail post-fact — leaving the user
    with naked Prophet exposure. The check fails closed BEFORE Prophet
    sees a quote.
    """
    # Total visible bid depth: 5 * 0.50 = $2.50 USDC. Target hedge: $50.
    thin_book = _book(bids=[(0.50, 5.0)], asks=[(0.52, 5.0)])

    result = assess_polymarket_depth(
        book_payload=thin_book,
        target_size_usdc=50.0,
        hedge_side="sell",  # we're selling YES on Polymarket to hedge a Prophet YES buy
        max_slippage_bps=200.0,
    )

    assert isinstance(result, DepthAssessment)
    assert result.sufficient is False
    assert result.reason == "insufficient_depth"
    # Fillable size must reflect what the book CAN absorb, so the operator
    # sees the actual shortfall.
    assert result.fillable_size_usdc == pytest.approx(2.5)
    assert result.target_size_usdc == pytest.approx(50.0)


def test_depth_check_accepts_deep_book_within_slippage() -> None:
    """Sufficient depth at acceptable slippage → opportunity flows through.

    The book has $500 of bid depth across two levels, well above the
    $50 target. The first level alone covers the trade, so average fill
    price equals the best bid — zero slippage.
    """
    deep_book = _book(
        bids=[(0.50, 1000.0), (0.49, 1000.0)],
        asks=[(0.51, 1000.0)],
    )

    result = assess_polymarket_depth(
        book_payload=deep_book,
        target_size_usdc=50.0,
        hedge_side="sell",
        max_slippage_bps=200.0,
    )

    assert result.sufficient is True
    assert result.reason == "ok"
    assert result.fillable_size_usdc >= 50.0
    assert result.realized_slippage_bps == pytest.approx(0.0, abs=1.0)


def test_depth_check_rejects_when_slippage_exceeds_cap() -> None:
    """Depth covers but avg fill price walks too far from best bid → reject.

    `max_slippage_bps` exists because filling deep into a book costs more
    than the headline best-bid price suggests. If the bot ignored this
    and traded anyway, the hedge would lock in a worse-than-quoted
    Polymarket leg and the realized spread vs the Prophet leg would be
    materially worse than the scored spread.
    """
    # Best bid is 0.50 but most of the depth is at 0.40 — 1000 bps below.
    skewed_book = _book(
        bids=[(0.50, 1.0), (0.40, 1000.0)],
        asks=[(0.55, 100.0)],
    )

    # Target is $50: 1.0 shares at 0.50 covers $0.50; the rest must come
    # from the 0.40 level (~123 shares at 0.40 = $49.50 of value).
    # Average fill price collapses near 0.40, ~2000 bps below best bid.
    result = assess_polymarket_depth(
        book_payload=skewed_book,
        target_size_usdc=50.0,
        hedge_side="sell",
        max_slippage_bps=200.0,
    )

    assert result.sufficient is False
    assert result.reason == "excess_slippage"
    assert result.realized_slippage_bps > 200.0


# ---------------------------------------------------------------------------
# 2. Post-fill hedge submission


@dataclass
class _StubProphetOrder:
    """Minimal stand-in for `prophet.orders.ProphetOrder`. Only the
    fields the hedger reads."""

    order_id: str
    market_id: str
    outcome: str  # "yes" | "no"
    side: str  # "buy" | "sell" — Prophet side, hedge takes the opposite
    shares: float  # Prophet fill quantity
    filled_shares: float
    limit_price: float
    status: str  # "FILLED" | "OPEN" | "PARTIAL"


class _StubHedger:
    """In-memory hedge executor. Records submissions for assertion."""

    def __init__(self, *, fail_on_submit: bool = False) -> None:
        self.submitted: list[dict] = []
        self.unwound: list[str] = []
        self._fail_on_submit = fail_on_submit

    def submit_hedge(
        self,
        *,
        condition_id: str,
        hedge_side: str,
        size_usdc: float,
        marketable_price: float,
    ) -> dict:
        if self._fail_on_submit:
            raise RuntimeError("polymarket_clob_rejected: book moved")
        self.submitted.append(
            {
                "condition_id": condition_id,
                "hedge_side": hedge_side,
                "size_usdc": size_usdc,
                "marketable_price": marketable_price,
            }
        )
        return {
            "polymarket_order_id": "POLY-1234",
            "filled_qty": size_usdc / marketable_price,
            "fill_price": marketable_price,
        }

    def unwind_prophet(self, *, order_id: str) -> None:
        self.unwound.append(order_id)


def test_hedge_submits_opposite_side_polymarket_order_on_prophet_fill() -> None:
    """A filled Prophet BUY YES order produces a Polymarket SELL YES hedge.

    The arb-bot is single-leg on Prophet under Mode A. With delta-neutral
    enabled, every Prophet fill triggers an offsetting Polymarket order
    immediately — same size, opposite side, marketable price snapped to
    the live order book. Without this, the user holds a Prophet YES
    position unhedged against Polymarket consensus.
    """
    filled = _StubProphetOrder(
        order_id="PRO-7",
        market_id="m1",
        outcome="yes",
        side="buy",
        shares=50.0,
        filled_shares=50.0,
        limit_price=0.42,
        status="FILLED",
    )
    hedger = _StubHedger()

    outcome = hedge_filled_order(
        prophet_order=filled,
        polymarket_condition_id="cond-abc",
        hedger=hedger,
        marketable_price=0.45,
    )

    assert isinstance(outcome, HedgeOutcome)
    assert outcome.hedge_status == "hedged"
    assert outcome.polymarket_order_id == "POLY-1234"
    assert outcome.error is None

    # Side flipped: Prophet BUY YES → Polymarket SELL YES.
    assert len(hedger.submitted) == 1
    assert hedger.submitted[0]["hedge_side"] == "sell"
    assert hedger.submitted[0]["size_usdc"] == pytest.approx(50.0)
    assert hedger.submitted[0]["condition_id"] == "cond-abc"
    assert hedger.unwound == []


# ---------------------------------------------------------------------------
# 3. Hedge failure → Prophet unwind


def test_hedge_failure_invokes_prophet_unwind_and_records_naked_status() -> None:
    """If the Polymarket hedge submission throws, the hedger:
      1. Records the failure as `naked_exposure` (the Prophet leg is filled
         and we can't cancel a filled order on the maker side).
      2. Invokes the unwind callback on the Prophet order so the operator
         dashboard surfaces the broken hedge for manual action.

    Without this path, a hedge failure produces a silent half-position
    that drifts off the operator's radar until the next P&L snapshot.
    """
    filled = _StubProphetOrder(
        order_id="PRO-8",
        market_id="m2",
        outcome="yes",
        side="buy",
        shares=25.0,
        filled_shares=25.0,
        limit_price=0.55,
        status="FILLED",
    )
    hedger = _StubHedger(fail_on_submit=True)

    outcome = hedge_filled_order(
        prophet_order=filled,
        polymarket_condition_id="cond-xyz",
        hedger=hedger,
        marketable_price=0.58,
    )

    assert outcome.hedge_status == "naked_exposure"
    assert outcome.polymarket_order_id is None
    assert outcome.error is not None
    assert "polymarket_clob_rejected" in outcome.error
    # The hedger must have asked Prophet to clean up whatever it can.
    assert hedger.unwound == ["PRO-8"]
