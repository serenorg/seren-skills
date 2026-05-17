"""Issue #605 — V2 onboarding orchestrator is idempotent.

The 4-step pipeline must skip every step where on-chain state already
shows it's done. Without idempotency:
  * Step 1 (createProxy) reverts with `create2 call failed` on the
    second invocation, leaving the cycle stuck at `blocked`.
  * Step 2 (USDC.e transfer) double-funds the proxy, wasting EOA
    USDC.e until the next wrap.
  * Step 3 (MultiSend approve batch) wastes ~600k gas per cycle
    re-approving spenders that already have MAX_UINT256 allowance.
  * Step 4 (wrap) double-wraps and reverts (or worse, succeeds and
    silently drains all USDC.e the operator deposited next).

This test mocks the on-chain probes to report "everything already
done" and asserts the orchestrator returns `skipped_already_onboarded`
without invoking the broadcaster — the single critical contract that
proves the steady-state cycle is a constant-cost no-op for onboarded
wallets.
"""

from __future__ import annotations

import pytest


def test_orchestrator_skips_all_steps_when_already_onboarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For an already-onboarded wallet:
      * proxy has code (deployed)
      * proxy holds pUSD (>= target)
      * all pUSD allowances are MAX_UINT256

    The orchestrator must run zero broadcasts and return a
    `skipped_already_onboarded` status. This is the steady-state behavior
    on every live cycle after the first successful onboarding.
    """
    import polymarket_v2_broadcast as broadcast_mod

    eoa = "0xAE10914F91E122D73aBFA651c64302EFB8cb9A04"
    proxy = "0xf5824d9B7E7ad2eC36dF19915067613111BE3e10"

    # Mock on-chain probes to report "fully onboarded".
    def fake_fetch_proxy_address(*, eoa_address, **kwargs):
        return proxy

    def fake_fetch_eth_get_code(address, **kwargs):
        # Non-empty code = proxy deployed.
        return "0x60806040..."

    def fake_fetch_erc20_balance(*, token, owner, **kwargs):
        # Proxy holds plenty of pUSD; EOA balance is irrelevant.
        from polymarket_v2 import POLYGON_PUSD
        if token.lower() == POLYGON_PUSD.lower() and owner.lower() == proxy.lower():
            return 10_000_000_000  # 10,000 pUSD in raw units (6 decimals)
        return 0

    def fake_fetch_erc20_allowance(*, token, owner, spender, **kwargs):
        # All approvals already at MAX_UINT256.
        return 2**256 - 1

    fake_broadcast_calls: list[str] = []

    def fake_broadcast_create_proxy(**kwargs):
        fake_broadcast_calls.append("create_proxy")
        return {"status": "submitted", "tx_hash": "0xfake"}

    def fake_broadcast_transfer(**kwargs):
        fake_broadcast_calls.append("transfer")
        return {"status": "submitted", "tx_hash": "0xfake"}

    def fake_broadcast_approve_batch(**kwargs):
        fake_broadcast_calls.append("approve_batch")
        return {"status": "submitted", "tx_hash": "0xfake"}

    def fake_broadcast_wrap(**kwargs):
        fake_broadcast_calls.append("wrap")
        return {"status": "submitted", "tx_hash": "0xfake"}

    monkeypatch.setattr(broadcast_mod, "fetch_proxy_address_for_eoa", fake_fetch_proxy_address)
    monkeypatch.setattr(broadcast_mod, "fetch_eth_get_code", fake_fetch_eth_get_code)
    monkeypatch.setattr(broadcast_mod, "fetch_erc20_balance_raw", fake_fetch_erc20_balance)
    monkeypatch.setattr(broadcast_mod, "fetch_erc20_allowance_raw", fake_fetch_erc20_allowance)
    monkeypatch.setattr(broadcast_mod, "_broadcast_create_proxy", fake_broadcast_create_proxy)
    monkeypatch.setattr(broadcast_mod, "_broadcast_usdc_e_transfer", fake_broadcast_transfer)
    monkeypatch.setattr(broadcast_mod, "_broadcast_safe_exec_transaction", fake_broadcast_approve_batch)
    # _broadcast_safe_exec_transaction is reused for both approve batch and wrap;
    # the orchestrator threads different `to` addresses through. We don't need a
    # separate stub for wrap — the same fake records both calls. But we want to
    # be sure neither approve_batch nor wrap fires.

    result = broadcast_mod.onboard_polymarket_v2(
        eoa_address=eoa,
        eoa_private_key="0x" + "11" * 32,
        target_usdc_e_raw=100_000_000,  # 100 USDC.e
    )

    assert result["status"] == "skipped_already_onboarded", (
        f"expected skipped_already_onboarded; got {result['status']!r} with "
        f"transactions {fake_broadcast_calls}"
    )
    assert fake_broadcast_calls == [], (
        f"expected zero broadcasts for fully-onboarded wallet; got {fake_broadcast_calls}"
    )
