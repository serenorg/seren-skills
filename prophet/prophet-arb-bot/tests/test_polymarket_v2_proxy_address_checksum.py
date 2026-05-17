"""Issue #613 — V2 onboarding TypeError on steps 3 + 4.

`fetch_proxy_address_for_eoa` decoded the proxy address as raw lowercase
hex. Once steps 3 + 4 threaded that lowercase string into
`Account.sign_transaction` as the transaction `to` field, eth-account
0.10+ raised `TypeError: Transaction had invalid fields: {'to': ...}`
because its `DynamicFeeTransaction.assert_valid_fields` rejects non-
checksum string addresses.

Effect: every cycle after a wallet's proxy was deployed (step 1 done)
silently blocked auto-discover seeding — `polymarket_v2_onboarding`
returned `exception:TypeError` with no actionable detail, the cycle still
reported `status=ok`, and the operator had no way to diagnose.

This is the single critical test for the fix: the seam that returns the
proxy address must produce a checksum-cased string, so every downstream
consumer (eth-account, `_build_and_send_tx`, the SafeTx digest) gets the
canonical form. Locking the contract here prevents regression if anyone
"simplifies" the function later by dropping `.lower()` -> bytes -> hex
roundtrips without thinking about eth-account's validator.

Idempotency, calldata correctness, EIP-712 digest, and selector pinning
are already covered by the existing V2 test suite — no duplication.
"""

from __future__ import annotations

import pytest


def test_fetch_proxy_address_returns_checksum_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory returns a right-padded 32-byte word whose last 20
    bytes are the proxy address. Our decoder must emit the EIP-55
    checksum form, not raw lowercase hex.

    Locking this contract at the seam means every transaction-signing
    call site downstream — `_broadcast_safe_exec_transaction` and any
    future probe that uses the proxy as a transaction `to` — gets a
    string that eth-account will accept without raising TypeError.
    """
    import polymarket_v2_broadcast as broadcast_mod

    # Simulate the factory's eth_call response. The address payload here
    # is intentionally written in lowercase hex (mirroring what
    # `_seren_polygon_rpc` actually returns from Polygon RPC) so the
    # test exercises the decoder, not the input casing.
    factory_response = "0x" + "00" * 12 + "f5824d9b7e7ad2ec36df19915067613111be3e10"

    monkeypatch.setattr(
        broadcast_mod,
        "_seren_polygon_rpc",
        lambda **kwargs: factory_response,
    )

    proxy = broadcast_mod.fetch_proxy_address_for_eoa(
        eoa_address="0xAE10914F91E122D73aBFA651c64302EFB8cb9A04",
    )

    # The canonical EIP-55 checksum of f5824d9b7e7ad2ec36df19915067613111be3e10
    # mixes case based on a keccak256 of the lowercase hex. We assert the
    # exact expected casing rather than just "not all lowercase" so a
    # regression that returns the wrong checksum (e.g. uppercasing
    # everything) also fails this test.
    assert proxy == "0xf5824d9B7E7ad2eC36dF19915067613111BE3e10", (
        f"expected EIP-55 checksum form; got {proxy!r}"
    )
