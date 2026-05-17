"""Issue #620 — V2 onboarding must wrap ALL available EOA USDC.e, not just
the 1-USDC fallback.

The previous `agent.py:651` derived `target_usdc_e_raw` from
`polymarket_avail_usdc * 10**6`. `polymarket_avail_usdc` is the CLOB-
tracked collateral balance, which is 0 until pUSD is actually deposited
into Polymarket CLOB — so on every fresh wallet the target collapsed to
the `or 1_000_000` floor. The orchestrator dutifully transferred and
wrapped exactly 1 USDC, declared `skipped_already_onboarded` for every
subsequent cycle, and stranded the operator's remaining EOA balance.
Auto-discover seed-preflight then stayed blocked at
`polymarket_deficit=50.0_usdc` indefinitely.

The fix is the "EOA on-chain USDC.e balance, full wrap" strategy chosen
in #620: compute target as the total collateral pool the operator has
funded — `proxy_pusd + proxy_usdc_e + eoa_usdc_e`. The orchestrator's
existing step-2 deficit math then transfers ALL EOA USDC.e (because
`deficit = target - proxy_collateral = eoa_usdc_e`), and step 4 wraps
the freshly-transferred amount. Idempotency holds: on subsequent cycles
with no new EOA funding the target equals the proxy's current
collateral, which the skip conditions treat as already done.

These are the single critical tests for the new seam — they pin the
sum semantic and the proxy-unavailable edge. Idempotency, calldata,
EIP-712, selector pinning, and #613/#617 invariants are already
covered by the existing V2 test suite. No duplication.
"""

from __future__ import annotations

import pytest


def test_compute_wrap_all_target_sums_proxy_collateral_plus_eoa_balance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Target = proxy_pusd + proxy_usdc_e + eoa_usdc_e (uint256 sum).

    Locking this against an asymmetric fixture (each leg a distinct
    non-zero value, with the proxy and the EOA holding different
    things) proves the helper:
      1. reads pUSD from the PROXY (not the EOA), and
      2. reads USDC.e from BOTH the proxy and the EOA, and
      3. sums them rather than returning any single leg or max().

    A regression that, say, returns `eoa_usdc_e` alone, would silently
    re-introduce the 1-USDC floor for wallets where the proxy already
    carries pUSD from a partial earlier wrap. The asymmetric fixture
    catches that drift.
    """
    import polymarket_v2_broadcast as broadcast_mod
    from polymarket_v2 import POLYGON_PUSD, POLYGON_USDC_E

    eoa_address = "0xAE10914F91E122D73aBFA651c64302EFB8cb9A04"
    proxy_address = "0x312eEa8c2598e5d1CD932F480bEC6dE1DA093A24"

    proxy_pusd_raw = 1_000_000  # 1 pUSD already wrapped on the proxy
    proxy_usdc_e_raw = 250_000  # 0.25 USDC.e parked on the proxy
    eoa_usdc_e_raw = 108_360_000  # ~$108.36 USDC.e still on the EOA

    def fake_balance(*, token, owner, **_kwargs):
        token_lc = token.lower()
        owner_lc = owner.lower()
        if token_lc == POLYGON_PUSD.lower() and owner_lc == proxy_address.lower():
            return proxy_pusd_raw
        if token_lc == POLYGON_USDC_E.lower() and owner_lc == proxy_address.lower():
            return proxy_usdc_e_raw
        if token_lc == POLYGON_USDC_E.lower() and owner_lc == eoa_address.lower():
            return eoa_usdc_e_raw
        # Anything else is a wiring bug — make it loud.
        raise AssertionError(
            f"unexpected balance probe: token={token} owner={owner}"
        )

    monkeypatch.setattr(broadcast_mod, "fetch_erc20_balance_raw", fake_balance)

    target = broadcast_mod.compute_wrap_all_target_usdc_e_raw(
        eoa_address=eoa_address,
        proxy_address=proxy_address,
    )

    expected = proxy_pusd_raw + proxy_usdc_e_raw + eoa_usdc_e_raw
    assert target == expected, (
        f"target drifted from the wrap-all-EOA semantic: "
        f"got {target}, expected {expected} "
        f"(proxy_pusd={proxy_pusd_raw} + proxy_usdc_e={proxy_usdc_e_raw} "
        f"+ eoa_usdc_e={eoa_usdc_e_raw})"
    )


def test_compute_wrap_all_target_returns_zero_when_proxy_address_unavailable() -> None:
    """When the proxy address lookup failed upstream (transport error),
    return 0 so the orchestrator's own preflight surfaces the canonical
    `proxy_address_unavailable` status. Returning a non-zero target
    here would mask the upstream RPC failure and short-circuit the
    diagnostic path.
    """
    import polymarket_v2_broadcast as broadcast_mod

    target = broadcast_mod.compute_wrap_all_target_usdc_e_raw(
        eoa_address="0xAE10914F91E122D73aBFA651c64302EFB8cb9A04",
        proxy_address=None,
    )

    assert target == 0, f"expected 0 on proxy_unavailable; got {target}"
