"""Issue #592 (Phase 1) — distinguish polymarket no-balance vs no-approvals.

Before #592, the two-venue funds preflight reported
`polymarket_available_usdc: 0.0` whenever the Polymarket CLOB returned
`balance: "0"` — without distinguishing two very different failure
modes:

  * `no_balance`  — the address truly has 0 USDC.e on-chain. The
    operator must deposit USDC.e to the address.
  * `no_approvals` — the address holds USDC.e but has not granted
    allowances to Polymarket's exchange contracts. The operator must
    grant approvals (one-time setup) — no new deposit is needed.

The operator-facing fix sent them to deposit funds that already existed
on their wallet. The diagnostic helper classifies the failure correctly
so the blocked envelope tells the truth.

Phase 1 scope (this PR): the classifier. The auto-submit of approval
txs is deferred to a follow-up because the spender addresses returned
by the live CLOB diverged from what `scripts/polymarket_live.py` pins
as constants; submitting `approve()` to unvalidated addresses would
expand the trust surface inappropriately.
"""

from __future__ import annotations

import pytest

from polymarket_state import (
    POLYMARKET_STATE_NO_APPROVALS,
    POLYMARKET_STATE_NO_BALANCE,
    POLYMARKET_STATE_OK,
    classify_polymarket_collateral_state,
)


def test_classifier_returns_ok_when_clob_sees_spendable_collateral() -> None:
    """Happy path: the CLOB reports non-zero spendable collateral —
    nothing to diagnose, just proceed."""
    state = classify_polymarket_collateral_state(
        clob_balance_usdc=42.0,
        on_chain_usdc_e=42.0,
    )

    assert state.kind == POLYMARKET_STATE_OK
    assert state.spendable_usdc == 42.0


def test_classifier_returns_no_balance_when_both_sources_zero() -> None:
    """Genuine zero balance — the operator must deposit USDC.e."""
    state = classify_polymarket_collateral_state(
        clob_balance_usdc=0.0,
        on_chain_usdc_e=0.0,
    )

    assert state.kind == POLYMARKET_STATE_NO_BALANCE
    assert state.spendable_usdc == 0.0
    assert "deposit" in state.remediation.lower()


def test_classifier_returns_no_approvals_when_balance_exists_but_clob_says_zero() -> None:
    """The exact bug from #592: the address holds USDC.e but the CLOB
    reports 0 spendable. Diagnose as no_approvals and tell the operator
    to grant allowances — NOT to deposit more funds."""
    state = classify_polymarket_collateral_state(
        clob_balance_usdc=0.0,
        on_chain_usdc_e=109.54,
    )

    assert state.kind == POLYMARKET_STATE_NO_APPROVALS
    # Spendable is what the CLOB sees today (zero), not the on-chain
    # holding — the latter is unusable until approvals are granted.
    assert state.spendable_usdc == 0.0
    assert state.on_chain_usdc_e == 109.54
    # The remediation must point at approvals, not deposits.
    assert "approv" in state.remediation.lower()
    assert "polymarket.com/wallet" in state.remediation.lower()


def test_classifier_handles_partial_approval_state() -> None:
    """If CLOB sees some collateral but less than on-chain balance, the
    operator has partially approved spenders. Still classify as OK if
    the CLOB sees enough to trade, and let the funds preflight decide
    if the amount is sufficient for the planned orders."""
    state = classify_polymarket_collateral_state(
        clob_balance_usdc=50.0,
        on_chain_usdc_e=109.54,
    )

    assert state.kind == POLYMARKET_STATE_OK
    assert state.spendable_usdc == 50.0


def test_classifier_handles_missing_on_chain_reading() -> None:
    """If the on-chain RPC probe fails (returns None), fall back to
    treating CLOB balance as authoritative. Don't claim no_approvals
    without evidence — that would mis-diagnose the operator."""
    state = classify_polymarket_collateral_state(
        clob_balance_usdc=0.0,
        on_chain_usdc_e=None,
    )

    # Without a working on-chain probe we can't distinguish the two
    # failure modes — fall back to the CLOB's verdict.
    assert state.kind == POLYMARKET_STATE_NO_BALANCE
    assert state.on_chain_usdc_e is None


# ---------------------------------------------------------------------------
# balanceOf calldata builder — critical because a wrong selector or
# padding would silently read garbage data from USDC.e and mis-diagnose
# the operator. One test covers the entire surface.


def test_balance_of_calldata_is_selector_plus_padded_address() -> None:
    """Verify the calldata encodes `balanceOf(address)` correctly:
    selector 0x70a08231 + address left-padded to 32 bytes.

    This is the only piece of polymarket_live.fetch_on_chain_usdc_e_balance
    that's worth a unit test — the rest is straightforward seren-polygon
    plumbing already covered by integration with the publisher.
    """
    from polymarket_live import _usdc_e_balance_of_calldata

    # 20-byte address. Result must be exactly 4 bytes selector +
    # 32 bytes padded arg = 36 bytes = 72 hex chars (plus '0x' prefix).
    result = _usdc_e_balance_of_calldata("0xAE10914F91E122D73aBFA651c64302EFB8cb9A04")

    assert result == "0x70a08231000000000000000000000000ae10914f91e122d73abfa651c64302efb8cb9a04"
    # 0x + 4-byte selector + 32-byte padded address = 2 + 8 + 64 = 74 chars.
    assert len(result) == 74
