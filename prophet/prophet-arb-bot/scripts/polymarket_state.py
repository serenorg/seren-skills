"""Polymarket collateral-state classifier (#592 phase 1).

The Polymarket CLOB's `get_balance_allowance(COLLATERAL)` returns
`balance: "0"` whenever the operator's address has not granted token
allowances to Polymarket's exchange contracts — even when the raw
on-chain USDC.e balance is sufficient. Reporting "Polymarket has $0"
in that case sends operators to deposit funds that already exist on
their wallet.

This classifier reads BOTH signals and distinguishes the failure modes:

  * `ok`            — CLOB sees spendable collateral; proceed.
  * `no_balance`    — On-chain USDC.e is genuinely zero; deposit needed.
  * `no_approvals`  — On-chain USDC.e exists but CLOB sees zero;
                      approvals needed (one-time setup on Polymarket).

Phase 1 scope is the classifier and a stable remediation string. The
auto-submit of `approve()` transactions is deferred to a follow-up PR
because validating the spender addresses returned by the live CLOB
against the constants pinned in `polymarket_live.py` is its own piece
of work, and shipping signed approvals to unvalidated addresses would
inappropriately expand the trust surface of the skill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


POLYMARKET_STATE_OK = "ok"
POLYMARKET_STATE_NO_BALANCE = "no_balance"
POLYMARKET_STATE_NO_APPROVALS = "no_approvals"


@dataclass
class PolymarketCollateralState:
    """Result of `classify_polymarket_collateral_state`.

    `spendable_usdc` is what the CLOB sees today — always use this for
    funds preflight math, not `on_chain_usdc_e`. The on-chain reading
    is informational, surfaced only so the agent's remediation message
    can mention the held-but-unspendable amount.
    """

    kind: str
    spendable_usdc: float
    on_chain_usdc_e: Optional[float]
    remediation: str


_REMEDIATION_NO_BALANCE = (
    "Polymarket reports zero spendable collateral and the on-chain "
    "USDC.e balance for this address is also zero. Deposit USDC.e on "
    "Polygon (token contract 0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174) "
    "to the configured POLY_ADDRESS to fund the hedge leg."
)

_REMEDIATION_NO_APPROVALS = (
    "Polymarket reports zero spendable collateral but the on-chain "
    "USDC.e balance is non-zero. The CLOB cannot move these funds "
    "because the address has not granted token allowances to the "
    "Polymarket exchange contracts. Grant the standard Polymarket "
    "approvals by visiting https://polymarket.com/wallet (one-time "
    "setup) — no new deposit is needed."
)

_REMEDIATION_OK = "Polymarket reports sufficient spendable collateral."


def classify_polymarket_collateral_state(
    *,
    clob_balance_usdc: float,
    on_chain_usdc_e: Optional[float],
) -> PolymarketCollateralState:
    """Decide which Polymarket collateral state the operator is in.

    Args:
        clob_balance_usdc: What `DirectClobTrader.get_cash_balance()`
            returned. This is what the CLOB will let the operator
            spend — already approval-filtered.
        on_chain_usdc_e: Raw USDC.e `balanceOf(POLY_ADDRESS)` from the
            on-chain `seren-polygon` query. `None` if the probe failed
            or wasn't attempted — in which case we fall back to the
            CLOB's verdict without claiming `no_approvals`.

    Returns:
        PolymarketCollateralState with `kind`, `spendable_usdc`, the
        on-chain reading (informational only), and a human-readable
        remediation string the agent can surface to the operator.
    """
    spendable = max(0.0, float(clob_balance_usdc))
    on_chain = on_chain_usdc_e if on_chain_usdc_e is None else max(0.0, float(on_chain_usdc_e))

    if spendable > 0.0:
        # The CLOB sees something — leave the sufficiency question to
        # the funds preflight. Classifier just says "no diagnostic
        # gap to flag."
        return PolymarketCollateralState(
            kind=POLYMARKET_STATE_OK,
            spendable_usdc=spendable,
            on_chain_usdc_e=on_chain,
            remediation=_REMEDIATION_OK,
        )

    # spendable == 0 from here on.
    if on_chain is not None and on_chain > 0.0:
        # The smoking gun: balance exists but the CLOB can't see it.
        # Approvals are missing.
        return PolymarketCollateralState(
            kind=POLYMARKET_STATE_NO_APPROVALS,
            spendable_usdc=spendable,
            on_chain_usdc_e=on_chain,
            remediation=_REMEDIATION_NO_APPROVALS,
        )

    # spendable == 0 AND (on_chain == 0 OR on_chain is None). Treat as
    # genuine no-balance — without a working on-chain probe we can't
    # prove the address holds USDC.e, so don't mis-diagnose as
    # `no_approvals` (which would send the operator to grant allowances
    # for funds they don't have).
    return PolymarketCollateralState(
        kind=POLYMARKET_STATE_NO_BALANCE,
        spendable_usdc=spendable,
        on_chain_usdc_e=on_chain,
        remediation=_REMEDIATION_NO_BALANCE,
    )
