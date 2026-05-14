"""Pure-function funds preflight for the arb-bot run loop.

Issue #524: `placeOrder` locks `size_usdc` of protocol cash per LIMIT
order. The cycle should not attempt N orders if the wallet can't cover
their combined collateral; route to a structured `deposit_required`
block instead.

Kept as a pure function so the unit test can exercise it without
spinning up a full `cmd_run` orchestration (which would require
mocking polymarket prices, JWT acquisition, order client, scoring,
and the recorder). The orchestration test belongs in a separate
file when one is justified.

Deposit constants are surfaced here so the arb-bot's deposit-required
envelope carries the same fields the bounty-runner emits. Prophet
runs on Polygon mainnet (chainId 137, verified live 2026-05-13).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol


DEPOSIT_CHAIN = "polygon"
DEPOSIT_CHAIN_ID = 137
DEPOSIT_USDC_CONTRACT_POLYGON = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"


class _OpportunityLike(Protocol):
    size_usdc: float


@dataclass
class FundsPreflightResult:
    """Outcome of the funds preflight.

    `ok=True` lets the placement loop proceed unchanged. `ok=False`
    surfaces the structured deficit the arb-bot's `cmd_run` propagates
    into the blocked `CycleResult` payload (action=deposit_required).
    """

    ok: bool
    available_usdc: float
    needed_usdc: float
    deficit_usdc: float

    def to_deposit_envelope(self) -> dict:
        """Render the block payload the agent's deposit runbook consumes."""
        return {
            "chain": DEPOSIT_CHAIN,
            "chain_id": DEPOSIT_CHAIN_ID,
            "usdc_contract_polygon": DEPOSIT_USDC_CONTRACT_POLYGON,
            "available_usdc": self.available_usdc,
            "needed_usdc": self.needed_usdc,
            "deficit_usdc": self.deficit_usdc,
        }


def evaluate_funds_preflight(
    *,
    opportunities: Iterable[_OpportunityLike],
    available_usdc: float,
) -> FundsPreflightResult:
    """Compare `sum(opp.size_usdc)` against `available_usdc`.

    No I/O, no exceptions — pure math. `cmd_run` queries the balance
    via `MinimalProphetClient.cash_balance` and threads it in.
    """
    needed = round(sum(float(o.size_usdc) for o in opportunities), 6)
    available = float(available_usdc)
    deficit = round(max(0.0, needed - available), 6)
    return FundsPreflightResult(
        ok=deficit <= 0.0,
        available_usdc=available,
        needed_usdc=needed,
        deficit_usdc=deficit,
    )


# ---------------------------------------------------------------------------
# Two-venue preflight (#536 — delta-neutral mode)


@dataclass
class TwoVenueFundsPreflightResult:
    """Funds preflight for delta-neutral runs.

    Both legs of an opportunity lock collateral simultaneously: Prophet
    holds ``size_usdc`` for the LIMIT, and Polymarket needs USDC of equal
    notional in CLOB collateral to back the hedge fill. We surface the
    deficit per venue so the agent's deposit runbook (#524 extension)
    can route the operator to the right `Deposit USDC` UI.
    """

    ok: bool
    prophet_available_usdc: float
    polymarket_available_usdc: float
    prophet_needed_usdc: float
    polymarket_needed_usdc: float
    prophet_deficit_usdc: float
    polymarket_deficit_usdc: float

    def to_deposit_envelope(self) -> dict:
        return {
            "chain": DEPOSIT_CHAIN,
            "chain_id": DEPOSIT_CHAIN_ID,
            "usdc_contract_polygon": DEPOSIT_USDC_CONTRACT_POLYGON,
            "prophet_available_usdc": self.prophet_available_usdc,
            "polymarket_available_usdc": self.polymarket_available_usdc,
            "prophet_needed_usdc": self.prophet_needed_usdc,
            "polymarket_needed_usdc": self.polymarket_needed_usdc,
            "prophet_deficit_usdc": self.prophet_deficit_usdc,
            "polymarket_deficit_usdc": self.polymarket_deficit_usdc,
        }


# ---------------------------------------------------------------------------
# Seed-bet preflight (#542 Fix 2)


@dataclass
class SeedFundsPreflightResult:
    """How many `pending_ui_submission` seeds the operator can fund.

    Mirrors the two-venue shape so the deposit runbook handles seeds and
    trading-orders with the same envelope branching. `ok=True` does NOT
    mean every candidate is funded — it means at least one is. The
    qualifier (`qualify_and_trim_pending`) is what actually filters the
    pending list down to `max_fundable_count`.
    """

    ok: bool
    max_fundable_count: int
    candidate_count: int
    initial_bet_usdc: float
    prophet_available_usdc: float
    polymarket_available_usdc: float
    prophet_deficit_usdc: float
    polymarket_deficit_usdc: float

    def to_deposit_envelope(self) -> dict:
        return {
            "chain": DEPOSIT_CHAIN,
            "chain_id": DEPOSIT_CHAIN_ID,
            "usdc_contract_polygon": DEPOSIT_USDC_CONTRACT_POLYGON,
            "prophet_available_usdc": self.prophet_available_usdc,
            "polymarket_available_usdc": self.polymarket_available_usdc,
            "prophet_deficit_usdc": self.prophet_deficit_usdc,
            "polymarket_deficit_usdc": self.polymarket_deficit_usdc,
            "max_fundable_count": self.max_fundable_count,
            "candidate_count": self.candidate_count,
            "initial_bet_usdc": self.initial_bet_usdc,
        }


def evaluate_seed_funds_preflight(
    *,
    candidate_count: int,
    initial_bet_usdc: float,
    prophet_available_usdc: float,
    polymarket_available_usdc: float,
) -> SeedFundsPreflightResult:
    """Decide how many seed bets the operator can fund right now.

    `initial_bet_usdc` is the per-candidate cost on EACH venue (Prophet
    seed + Polymarket hedge). The bottleneck is the thinner of the two
    balances divided by the per-seed cost, capped at the candidate
    count.

    A zero result returns ``ok=False`` and a deficit envelope sized to
    fully fund all candidates — the operator can read it off and route
    a deposit to whichever side is short.
    """
    if initial_bet_usdc <= 0.0:
        raise ValueError(
            f"initial_bet_usdc must be > 0; got {initial_bet_usdc}"
        )

    prophet_avail = max(0.0, float(prophet_available_usdc))
    polymarket_avail = max(0.0, float(polymarket_available_usdc))
    prophet_floor = int(prophet_avail // initial_bet_usdc)
    polymarket_floor = int(polymarket_avail // initial_bet_usdc)
    max_fundable = max(0, min(prophet_floor, polymarket_floor, int(candidate_count)))

    full_needed = float(initial_bet_usdc) * int(candidate_count)
    prophet_deficit = round(max(0.0, full_needed - prophet_avail), 6)
    polymarket_deficit = round(max(0.0, full_needed - polymarket_avail), 6)

    return SeedFundsPreflightResult(
        ok=max_fundable > 0,
        max_fundable_count=max_fundable,
        candidate_count=int(candidate_count),
        initial_bet_usdc=float(initial_bet_usdc),
        prophet_available_usdc=prophet_avail,
        polymarket_available_usdc=polymarket_avail,
        prophet_deficit_usdc=prophet_deficit,
        polymarket_deficit_usdc=polymarket_deficit,
    )


def evaluate_two_venue_funds_preflight(
    *,
    opportunities: Iterable[_OpportunityLike],
    prophet_available_usdc: float,
    polymarket_available_usdc: float,
) -> TwoVenueFundsPreflightResult:
    """Same math as ``evaluate_funds_preflight`` but returns split
    deficits across the Prophet and Polymarket venues.

    Delta-neutral notional is symmetric per opportunity: the Prophet
    LIMIT locks ``size_usdc`` of Prophet protocol cash, and a successful
    hedge will lock the same notional on the Polymarket CLOB.
    """
    needed = round(sum(float(o.size_usdc) for o in opportunities), 6)
    prophet_avail = float(prophet_available_usdc)
    polymarket_avail = float(polymarket_available_usdc)
    prophet_deficit = round(max(0.0, needed - prophet_avail), 6)
    polymarket_deficit = round(max(0.0, needed - polymarket_avail), 6)
    return TwoVenueFundsPreflightResult(
        ok=(prophet_deficit <= 0.0 and polymarket_deficit <= 0.0),
        prophet_available_usdc=prophet_avail,
        polymarket_available_usdc=polymarket_avail,
        prophet_needed_usdc=needed,
        polymarket_needed_usdc=needed,
        prophet_deficit_usdc=prophet_deficit,
        polymarket_deficit_usdc=polymarket_deficit,
    )
