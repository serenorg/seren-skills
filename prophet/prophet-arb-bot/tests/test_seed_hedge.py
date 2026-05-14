"""Delta-neutral seed hedge (#542 Fix 3).

The arb-bot already hedges *fills* in delta-neutral mode via
`hedge_filled_order`. The seed bet paid during Prophet's Phase 15 market
creation is a separate one-sided commitment — without a hedge it
resolves YES/NO with the market, which violates the bot's delta-neutral
contract.

`hedge_seed_bet` is the parallel entry point. The agent invokes it
immediately after confirming the Privy signing prompt for a given
`pending_ui_submission` entry. Behavior contract:

  - **Success:** submit the opposing Polymarket marketable order via
    the same `submit_hedge` path the trading-side hedger uses; return
    `hedge_status='hedged'` with the polymarket order id + fill data.
  - **Failure:** record `hedge_status='naked_exposure'` and surface
    the original submission error. Crucially, we do NOT try to unwind
    the Prophet seed — the market has already been created on confirm
    and there is no "cancel" primitive for it.

Same `Hedger` protocol the trading-side path uses, so the production
wiring is `DirectClobTrader` and the tests use an in-memory stub.
"""

from __future__ import annotations

from typing import Any

import pytest

from arbitrage.hedge import (
    HedgeOutcome,
    hedge_seed_bet,
)


class _StubHedger:
    def __init__(
        self,
        *,
        submit_response: dict | None = None,
        submit_error: Exception | None = None,
    ) -> None:
        self.submit_calls: list[dict[str, Any]] = []
        self.unwind_calls: list[dict[str, Any]] = []
        self._submit_response = submit_response
        self._submit_error = submit_error

    def submit_hedge(
        self,
        *,
        condition_id: str,
        hedge_side: str,
        size_usdc: float,
        marketable_price: float,
    ) -> dict[str, Any]:
        self.submit_calls.append(
            {
                "condition_id": condition_id,
                "hedge_side": hedge_side,
                "size_usdc": size_usdc,
                "marketable_price": marketable_price,
            }
        )
        if self._submit_error is not None:
            raise self._submit_error
        return self._submit_response or {}

    def unwind_prophet(self, *, order_id: str) -> None:
        # Should NEVER be called by hedge_seed_bet — Prophet has no
        # post-creation cancel for the seed. Tests assert this directly.
        self.unwind_calls.append({"order_id": order_id})


def test_seed_hedge_success_records_hedged_outcome() -> None:
    hedger = _StubHedger(
        submit_response={
            "polymarket_order_id": "POLY-abc",
            "filled_qty": 1.0,
            "fill_price": 0.42,
        }
    )

    outcome = hedge_seed_bet(
        prophet_market_id="PMI-1",
        polymarket_condition_id="0xCOND",
        prophet_seed_side="buy",  # bought YES on Prophet
        size_usdc=1.0,
        marketable_price=0.001,
        hedger=hedger,
    )

    assert isinstance(outcome, HedgeOutcome)
    assert outcome.hedge_status == "hedged"
    assert outcome.polymarket_order_id == "POLY-abc"
    assert outcome.polymarket_filled_qty == 1.0
    assert outcome.polymarket_fill_price == 0.42
    assert outcome.error is None

    # Hedge side is the OPPOSITE of the Prophet seed:
    assert hedger.submit_calls == [
        {
            "condition_id": "0xCOND",
            "hedge_side": "sell",
            "size_usdc": 1.0,
            "marketable_price": 0.001,
        }
    ]
    # No Prophet unwind for seeds — the market is already created.
    assert hedger.unwind_calls == []


def test_seed_hedge_failure_records_naked_exposure_no_unwind() -> None:
    """Phase 15 has already committed the Prophet seed at confirm-time.
    A hedge failure must NOT trigger `unwind_prophet` — there is no
    cancel for a committed seed."""
    hedger = _StubHedger(submit_error=RuntimeError("CLOB rejected"))

    outcome = hedge_seed_bet(
        prophet_market_id="PMI-2",
        polymarket_condition_id="0xCOND",
        prophet_seed_side="buy",
        size_usdc=1.0,
        marketable_price=0.001,
        hedger=hedger,
    )

    assert outcome.hedge_status == "naked_exposure"
    assert outcome.polymarket_order_id is None
    assert outcome.error is not None
    assert "CLOB rejected" in outcome.error
    # The critical assertion: we do NOT call unwind_prophet on the seed.
    assert hedger.unwind_calls == []


def test_seed_hedge_missing_order_id_is_naked_exposure() -> None:
    """If submit_hedge succeeds but returns no order id, treat as soft
    failure: naked exposure, no unwind."""
    hedger = _StubHedger(
        submit_response={"polymarket_order_id": "", "filled_qty": 0.0, "fill_price": 0.0}
    )

    outcome = hedge_seed_bet(
        prophet_market_id="PMI-3",
        polymarket_condition_id="0xCOND",
        prophet_seed_side="sell",
        size_usdc=1.0,
        marketable_price=0.999,
        hedger=hedger,
    )

    assert outcome.hedge_status == "naked_exposure"
    assert outcome.polymarket_order_id is None
    assert outcome.error == "polymarket_submit_returned_no_order_id"
    assert hedger.unwind_calls == []


def test_seed_hedge_uses_opposite_side_for_sell_seed() -> None:
    """If the agent seeds NO on Prophet (`sell` YES), the hedge buys YES
    on Polymarket."""
    hedger = _StubHedger(
        submit_response={
            "polymarket_order_id": "POLY-x",
            "filled_qty": 1.0,
            "fill_price": 0.6,
        }
    )

    hedge_seed_bet(
        prophet_market_id="PMI-4",
        polymarket_condition_id="0xCOND",
        prophet_seed_side="sell",
        size_usdc=1.0,
        marketable_price=0.999,
        hedger=hedger,
    )

    assert hedger.submit_calls[0]["hedge_side"] == "buy"
