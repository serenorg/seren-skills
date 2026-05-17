"""Issue #605 — V2 state classifier surfaces `no_proxy` honestly.

The V1 classifier (`polymarket_state.classify_polymarket_collateral_state`)
distinguished `no_balance` from `no_approvals` by comparing the CLOB
balance to the EOA's on-chain USDC.e. Post-V2 (2026-04-28), the CLOB
tracks pUSD held by the PROXY — the EOA's USDC.e is irrelevant. A
wallet with $1000 USDC.e on the EOA but no proxy deployed has CLOB
balance $0, and the existing classifier would mis-report this as
`no_approvals` and promise an auto-fix that the existing #596
broadcast path can't deliver (it approves from the EOA, not from a
proxy that doesn't exist yet).

This test pins the new behavior: `classify_polymarket_v2_state` returns
`no_proxy` when `proxy_has_code=False`, regardless of how much USDC.e
the EOA holds. That's the precondition the #605 onboarding orchestrator
checks before signing `createProxy`.
"""

from __future__ import annotations


def test_classifier_returns_no_proxy_when_proxy_has_no_code() -> None:
    """The smoking-gun V2 footgun: EOA has USDC.e, no proxy deployed,
    CLOB shows $0. The V1 classifier said `no_approvals` and promised
    the bot would auto-fix; for V2 wallets that promise is unkeepable
    without first deploying the proxy. The new classifier surfaces
    `no_proxy` so the V2 orchestrator runs the right recovery step."""
    from polymarket_v2 import (
        POLYMARKET_V2_STATE_NO_PROXY,
        classify_polymarket_v2_state,
    )

    state = classify_polymarket_v2_state(
        clob_balance_pusd=0.0,
        proxy_has_code=False,
        proxy_usdc_e_balance=None,
        proxy_pusd_balance=None,
        eoa_usdc_e_balance=109.54,
    )

    assert state.kind == POLYMARKET_V2_STATE_NO_PROXY
    # Spendable is what the CLOB sees today (zero) — even though the
    # EOA holds USDC.e. The deposit_required envelope must NOT tell the
    # operator to deposit more funds.
    assert state.spendable_pusd == 0.0
    assert state.eoa_usdc_e_balance == 109.54
    # Remediation must mention proxy deployment, not deposits or
    # approvals — those are downstream of proxy creation.
    remediation = state.remediation.lower()
    assert "proxy" in remediation
    assert "deposit" not in remediation
