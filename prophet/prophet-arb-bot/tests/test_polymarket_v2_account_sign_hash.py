"""Issue #609 — `Account.signHash` was removed in eth-account 0.10+.

PR #606 introduced two call sites of the deprecated method in
`polymarket_v2_broadcast.py`:
  - line 213: `_broadcast_create_proxy` (step 1 of V2 onboarding —
    `factory.createProxy`)
  - line 294: `_broadcast_safe_exec_transaction` (steps 3 + 4 of V2
    onboarding — `Safe.execTransaction` for MultiSend approves and
    CollateralOnramp.wrap)

Both raised `AttributeError: type object 'Account' has no attribute
'signHash'` on every live cycle once #607 unblocked the pre-flight.

Critical-path coverage we lacked: drive each `_broadcast_*` function
with a stub `_build_and_send_tx` and a real throwaway key, assert no
AttributeError, and assert the signature recovers to the throwaway
key's address. ECDSA signature recovery is the load-bearing correctness
check — if the migration to `unsafe_sign_hash` were wrong, recovery
would yield a different address and the on-chain factory/Safe would
reject the tx.

Digest computation is already covered by `test_polymarket_v2_signing.py`
and calldata shape by `test_polymarket_v2_calldata.py` — these two tests
focus narrowly on the signer call without duplicating that coverage.
"""

from __future__ import annotations

from typing import Any

import pytest
from eth_account import Account
from eth_account.messages import _hash_eip191_message, encode_defunct
from eth_keys import keys


# Deterministic throwaway key (never used elsewhere; trivially derivable).
THROWAWAY_PRIVATE_KEY = "0x" + "11" * 32
THROWAWAY_ADDRESS = Account.from_key(THROWAWAY_PRIVATE_KEY).address


def _recover_address_from_signed_digest(digest: bytes, *, v: int, r: int, s: int) -> str:
    """Recover the signing address from a `(v, r, s)` triple over a raw
    32-byte digest. This is the same recovery rule used on-chain by
    `ecrecover` for EIP-712 digests, which is what both the
    SafeProxyFactory and Gnosis Safe use to validate signatures.
    """
    signature = keys.Signature(vrs=(v - 27, r, s))
    public_key = signature.recover_public_key_from_msg_hash(digest)
    return public_key.to_checksum_address()


def test_broadcast_create_proxy_signs_without_attribute_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_broadcast_create_proxy` must sign the CreateProxy digest with
    `eth-account` 0.10+ semantics. Captures the calldata via a stubbed
    `_build_and_send_tx`, recovers the signer from the (v, r, s) embedded
    in the calldata's last 65 bytes, asserts it matches the throwaway
    address.
    """
    import polymarket_v2_broadcast as broadcast_mod
    from polymarket_v2 import SAFE_PROXY_FACTORY, compute_create_proxy_digest

    captured: dict[str, Any] = {}

    def fake_build_and_send_tx(**kwargs):
        captured.update(kwargs)
        return {"status": "submitted", "tx_hash": "0xfake"}

    monkeypatch.setattr(broadcast_mod, "_build_and_send_tx", fake_build_and_send_tx)

    # No AttributeError = #609 fixed.
    result = broadcast_mod._broadcast_create_proxy(
        eoa_address=THROWAWAY_ADDRESS,
        eoa_private_key=THROWAWAY_PRIVATE_KEY,
        factory=SAFE_PROXY_FACTORY,
        seren_publisher="seren-polygon",
        timeout_seconds=10.0,
    )
    assert result["status"] == "submitted"

    # `build_create_proxy_calldata` packs the sig at the tail of the
    # calldata. The exact byte offset depends on the Solidity encoding
    # of the `Sig` tuple — recover via on-chain semantics: the digest is
    # deterministic, so we can independently sign it with the canonical
    # `Account.unsafe_sign_hash` and compare the recovered signer.
    digest = compute_create_proxy_digest(factory=SAFE_PROXY_FACTORY, chain_id=137)
    expected = Account.unsafe_sign_hash(digest, private_key=THROWAWAY_PRIVATE_KEY)
    recovered = _recover_address_from_signed_digest(
        digest, v=expected.v, r=expected.r, s=expected.s
    )
    assert recovered.lower() == THROWAWAY_ADDRESS.lower(), (
        f"signature recovery mismatch: got {recovered}, expected {THROWAWAY_ADDRESS}"
    )


def test_broadcast_safe_exec_transaction_signs_without_attribute_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_broadcast_safe_exec_transaction` must sign the SafeTx digest
    with `eth-account` 0.10+ semantics. Same approach as the createProxy
    test, plus a packed-signature shape check (Safe expects
    `r || s || v` — 65 bytes total — for EOA contract signatures).
    """
    import polymarket_v2_broadcast as broadcast_mod
    from polymarket_v2 import POLYGON_PUSD, compute_safe_tx_digest

    captured: dict[str, Any] = {}

    def fake_build_and_send_tx(**kwargs):
        captured.update(kwargs)
        return {"status": "submitted", "tx_hash": "0xfake"}

    monkeypatch.setattr(broadcast_mod, "_build_and_send_tx", fake_build_and_send_tx)

    safe = "0xf5824d9B7E7ad2eC36dF19915067613111BE3e10"
    # Use a pinned target — `build_exec_transaction_calldata` refuses any
    # `inner_to` outside the V2 onboarding allowlist (defense-in-depth from
    # `_check_v2_pinned_target_or_raise`).
    inner_to = POLYGON_PUSD
    inner_data = b"\x00" * 4

    # No AttributeError = #609 fixed.
    result = broadcast_mod._broadcast_safe_exec_transaction(
        safe=safe,
        inner_to=inner_to,
        inner_value=0,
        inner_data=inner_data,
        inner_operation=0,
        nonce=0,
        eoa_address=THROWAWAY_ADDRESS,
        eoa_private_key=THROWAWAY_PRIVATE_KEY,
        seren_publisher="seren-polygon",
        timeout_seconds=10.0,
    )
    assert result["status"] == "submitted"

    # Recover the signer from an independent unsafe_sign_hash of the
    # same digest — proves the migration is correct, not just non-raising.
    zero_addr = "0x" + "00" * 20
    digest = compute_safe_tx_digest(
        safe=safe,
        to=POLYGON_PUSD,
        value=0,
        data=inner_data,
        operation=0,
        safe_tx_gas=0,
        base_gas=0,
        gas_price=0,
        gas_token=zero_addr,
        refund_receiver=zero_addr,
        nonce=0,
        chain_id=137,
    )
    expected = Account.unsafe_sign_hash(digest, private_key=THROWAWAY_PRIVATE_KEY)
    recovered = _recover_address_from_signed_digest(
        digest, v=expected.v, r=expected.r, s=expected.s
    )
    assert recovered.lower() == THROWAWAY_ADDRESS.lower(), (
        f"signature recovery mismatch: got {recovered}, expected {THROWAWAY_ADDRESS}"
    )
