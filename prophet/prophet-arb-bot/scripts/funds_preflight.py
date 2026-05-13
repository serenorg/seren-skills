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
