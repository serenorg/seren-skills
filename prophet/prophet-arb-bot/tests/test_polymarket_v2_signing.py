"""Issue #605 — EIP-712 signing for V2 onboarding.

Two distinct EIP-712 messages, both signed by the EOA private key:

  1. **CreateProxy** — signed for SafeProxyFactory's `createProxy`.
     Domain: `EIP712Domain(string name, uint256 chainId, address verifyingContract)`
     with `name="Polymarket Contract Proxy Factory"`,
     `verifyingContract=SafeProxyFactory`.
     Message: `CreateProxy(address paymentToken, uint256 payment, address paymentReceiver)`
     with all-zero fields.

  2. **SafeTx** — Gnosis Safe v1.1.1 typehash. Signed per execTransaction
     call. Domain: `EIP712Domain(uint256 chainId, address verifyingContract)`
     with `verifyingContract=<safe_proxy_address>`. Message: the
     `SafeTx(...)` 10-field struct.

Critical-only tests: each test asserts our implementation produces the
EXACT same digest as `eth_account.messages.encode_typed_data` — an
independent EIP-712 reference implementation. If our digest drifts from
the reference, the EOA signs a message the on-chain contract won't
recover the right address for, and the tx reverts.
"""

from __future__ import annotations

from eth_account.messages import encode_typed_data, _hash_eip191_message


# Test addresses chosen to NOT match any real Polymarket-related
# address — these are pure EIP-712 fixture inputs.
TEST_FACTORY = "0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b"
TEST_SAFE = "0x1234567890aBCdEF1234567890ABcDEf12345678"
CHAIN_ID = 137


def test_create_proxy_eip712_digest_matches_eth_account_reference() -> None:
    """The CreateProxy digest our code produces must equal the digest
    eth_account computes from the same typed-data spec. If we drift, the
    SafeProxyFactory will recover the wrong owner from the recovered
    signer and the deployed proxy will be unusable."""
    from polymarket_v2 import compute_create_proxy_digest

    domain = {
        "name": "Polymarket Contract Proxy Factory",
        "chainId": CHAIN_ID,
        "verifyingContract": TEST_FACTORY,
    }
    message_types = {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "CreateProxy": [
            {"name": "paymentToken", "type": "address"},
            {"name": "payment", "type": "uint256"},
            {"name": "paymentReceiver", "type": "address"},
        ],
    }
    message = {
        "paymentToken": "0x" + "00" * 20,
        "payment": 0,
        "paymentReceiver": "0x" + "00" * 20,
    }

    signable = encode_typed_data(
        full_message={
            "types": message_types,
            "primaryType": "CreateProxy",
            "domain": domain,
            "message": message,
        }
    )
    expected = _hash_eip191_message(signable)

    actual = compute_create_proxy_digest(
        factory=TEST_FACTORY,
        chain_id=CHAIN_ID,
    )

    assert actual == expected, (
        f"createProxy digest drift; reference={expected.hex()} actual={actual.hex()}"
    )


def test_safe_tx_eip712_digest_matches_eth_account_reference() -> None:
    """Gnosis Safe v1.1.1 SafeTx digest. We exercise the full 10-field
    struct because every field participates in the typehash; a single
    wrong padding silently re-targets the signature."""
    from polymarket_v2 import compute_safe_tx_digest

    to_addr = "0xA238CBeb142c10Ef7Ad8442C6D1f9E89e07e7761"  # MultiSend 1.3.0
    value = 0
    data = bytes.fromhex("deadbeefcafebabe")
    operation = 1  # DELEGATECALL
    safe_tx_gas = 0
    base_gas = 0
    gas_price = 0
    gas_token = "0x" + "00" * 20
    refund_receiver = "0x" + "00" * 20
    nonce = 7

    domain = {"chainId": CHAIN_ID, "verifyingContract": TEST_SAFE}
    message_types = {
        "EIP712Domain": [
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "SafeTx": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
            {"name": "operation", "type": "uint8"},
            {"name": "safeTxGas", "type": "uint256"},
            {"name": "baseGas", "type": "uint256"},
            {"name": "gasPrice", "type": "uint256"},
            {"name": "gasToken", "type": "address"},
            {"name": "refundReceiver", "type": "address"},
            {"name": "nonce", "type": "uint256"},
        ],
    }
    message = {
        "to": to_addr,
        "value": value,
        "data": data,
        "operation": operation,
        "safeTxGas": safe_tx_gas,
        "baseGas": base_gas,
        "gasPrice": gas_price,
        "gasToken": gas_token,
        "refundReceiver": refund_receiver,
        "nonce": nonce,
    }

    signable = encode_typed_data(
        full_message={
            "types": message_types,
            "primaryType": "SafeTx",
            "domain": domain,
            "message": message,
        }
    )
    expected = _hash_eip191_message(signable)

    actual = compute_safe_tx_digest(
        safe=TEST_SAFE,
        to=to_addr,
        value=value,
        data=data,
        operation=operation,
        safe_tx_gas=safe_tx_gas,
        base_gas=base_gas,
        gas_price=gas_price,
        gas_token=gas_token,
        refund_receiver=refund_receiver,
        nonce=nonce,
        chain_id=CHAIN_ID,
    )

    assert actual == expected, (
        f"SafeTx digest drift; reference={expected.hex()} actual={actual.hex()}"
    )
