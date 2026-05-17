"""Issue #617 — CollateralOnramp.wrap calldata must match the deployed
contract's 3-arg signature, not the 1-arg variant.

The on-chain CollateralOnramp at `0x93070a847efEf7F70739046A929D47a521F5B8ee`
(Polygon mainnet) exposes:

    wrap(address _asset, address _to, uint256 _amount)   selector 0x62355638

NOT the 1-arg `wrap(uint256)` (selector 0xea598cb0) the bot previously
encoded. The dispatcher rejects the wrong selector, eth_estimateGas
reverts, the orchestrator returns `gas_estimate_failed`, and the V2
onboarding pipeline stalls at step 4 — pUSD never lands on the proxy,
auto-discover seed-preflight stays at `polymarket_deficit=50.0_usdc`
indefinitely.

This is the single critical contract: the wrap leg must produce calldata
byte-exactly matching the on-chain ABI of `wrap(address,address,uint256)`.
Locking this against a real Polygonscan-decoded reference tx
(`0x04d51086…0d`) prevents any future drift from re-breaking the
onboarding pipeline.

Existing tests already cover idempotency, selector pinning for the
approve batch, EIP-712 digests, and proxy address checksum normalization
(#613). No duplication here.
"""

from __future__ import annotations

import pytest


def test_wrap_calldata_matches_three_arg_signature_byte_exact() -> None:
    """Byte-exact assertion against the live `wrap(address,address,uint256)`
    encoding observed in production tx
    `0x04d510860c317cfbb72666788dd8a5a27a8a7917b834573f7abf035639fd401d`
    (Polygon mainnet, decoded via `eth_getTransactionByHash`):

      selector  = 0x62355638
      arg1 (asset)  = USDC.e  = 0x2791bca1f2de4661ed88a30c99a7a9449aa84174
      arg2 (to)     = (Safe proxy recipient)
      arg3 (amount) = 0x36cf281  = 57_471_617 USDC.e raw (≈ $57.47)

    Both addresses are encoded left-padded to 32 bytes. Amount is encoded
    as a uint256 big-endian. No leading 0x in the body — only in the
    final returned string.
    """
    from polymarket_v2 import (
        POLYGON_USDC_E,
        build_collateral_onramp_wrap_calldata,
    )

    recipient = "0x76d4d4703add6e94cfdb1107f3d991d85ff2c512"
    amount_raw = 57_471_617

    calldata = build_collateral_onramp_wrap_calldata(
        asset=POLYGON_USDC_E,
        recipient=recipient,
        amount_raw=amount_raw,
    )

    expected = (
        "0x62355638"
        "0000000000000000000000002791bca1f2de4661ed88a30c99a7a9449aa84174"
        "00000000000000000000000076d4d4703add6e94cfdb1107f3d991d85ff2c512"
        "00000000000000000000000000000000000000000000000000000000036cf281"
    )
    assert calldata == expected, (
        f"wrap calldata drifted from on-chain ABI:\n"
        f"got      {calldata!r}\n"
        f"expected {expected!r}"
    )


def test_wrap_calldata_rejects_unpinned_asset() -> None:
    """Defense-in-depth: even though the recipient is the user's own
    proxy (not pinnable), the `_asset` parameter is a token contract
    address. CollateralOnramp may accept multiple input collaterals in
    the future; we want to keep the encoder anchored to the V2 pinned
    set so a future code path that accidentally passes an attacker-
    controlled token address can't sign a wrap that drains an arbitrary
    ERC-20 from the proxy.
    """
    from polymarket_v2 import build_collateral_onramp_wrap_calldata

    # An arbitrary address not in the V2 onboarding allowlist.
    attacker_token = "0x" + "de" * 20
    recipient = "0x" + "ab" * 20

    with pytest.raises(ValueError, match="refusing to encode"):
        build_collateral_onramp_wrap_calldata(
            asset=attacker_token,
            recipient=recipient,
            amount_raw=1_000_000,
        )
