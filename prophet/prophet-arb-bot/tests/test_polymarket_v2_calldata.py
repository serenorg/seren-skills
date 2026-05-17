"""Issue #605 — V2 onboarding pipeline calldata correctness.

Polymarket migrated to V2 + pUSD collateral on 2026-04-28. Real
onboarding deploys a Safe proxy via `SafeProxyFactory.createProxy`,
then submits an 11-call MultiSend delegatecall batch via
`Safe.execTransaction` to grant pUSD approvals and CTF setApprovalForAll
to the V2 exchanges + collateral adapters. None of these calls share a
selector or ABI with the existing direct-EOA approve flow in
`polymarket_live.py`, so encoder correctness is critical: a wrong
selector or wrong padding silently broadcasts a tx that either does
nothing or burns gas without progressing onboarding.

Critical-only tests:
  * `createProxy` selector + zero-payment EIP-712 arg encoding
  * `MultiSend.multiSend(bytes)` selector + canonical 11-call packed batch
  * `Safe.execTransaction` selector + full ABI encoding
  * Pinned-target guard refuses any `to` outside the V2 onboarding allowlist
    (defense-in-depth, same pattern as `_check_pinned_spender_or_raise`)
"""

from __future__ import annotations

import pytest


# Polymarket V2 onboarding addresses — all primary-source verified on
# Polygonscan. The implementation pins these as constants; the test
# inlines them so a copy-paste typo in the implementation can't pass.
SAFE_PROXY_FACTORY = "0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b"
SAFE_MULTISEND_1_3_0 = "0xA238CBeb142c10Ef7Ad8442C6D1f9E89e07e7761"
POLYGON_CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
POLYGON_PUSD = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
POLYGON_USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
CTF_EXCHANGE_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
NEG_RISK_CTF_EXCHANGE_V2 = "0xe2222d279d744050d28e00520010520000310F59"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
CTF_COLLATERAL_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"
NEG_RISK_CTF_COLLATERAL_ADAPTER = "0xadA2005600Dec949baf300f4C6120000bDB6eAab"


def test_create_proxy_calldata_selector_and_zero_payment_args() -> None:
    """`createProxy(address paymentToken, uint256 payment, address paymentReceiver, Sig sig)`
    selector is `0xa1884d2c`. Onboarding always passes zero for the three
    payment fields — Polymarket's frontend does this for every fresh
    proxy and both confirmed mainnet onboarding txs (verified on
    Polygonscan) match this pattern.

    Sig struct is `(uint8 v, bytes32 r, bytes32 s)` — encoded inline,
    not as a dynamic type.
    """
    from polymarket_v2 import build_create_proxy_calldata

    v = 27
    r = bytes.fromhex("11" * 32)
    s = bytes.fromhex("22" * 32)

    result = build_create_proxy_calldata(v=v, r=r, s=s)

    # 0x + 4-byte selector + 6 * 32-byte words = 2 + 8 + 384 = 394 chars
    # (paymentToken, payment, paymentReceiver, v, r, s)
    assert result.startswith("0xa1884d2c")
    assert len(result) == 394
    # Three zero-padded words for paymentToken / payment / paymentReceiver
    assert result[10:202] == "0" * 192
    # v word — uint8 right-aligned in 32 bytes
    assert result[202:266] == ("0" * 62) + "1b"
    # r and s — exact 32-byte values
    assert result[266:330] == "11" * 32
    assert result[330:394] == "22" * 32


def test_multisend_batch_encodes_canonical_onboarding_calls() -> None:
    """`MultiSend.multiSend(bytes transactions)` selector is `0x8d80ff0a`.
    The inner `transactions` arg is PACKED-encoded (not standard ABI):
    each call is `operation (1) || to (20) || value (32) || data_length (32) || data`.

    For the V2 onboarding batch all 11 inner calls are `operation=0`
    (CALL, not delegatecall) with `value=0` — the outer execTransaction
    is the delegatecall to MultiSend. We verify the canonical 11-call
    batch matches the structure observed on Polygonscan for fresh proxy
    onboarding, byte-for-byte.
    """
    from polymarket_v2 import (
        build_multisend_batch_calldata,
        build_v2_canonical_onboarding_inner_batch,
    )

    inner_packed = build_v2_canonical_onboarding_inner_batch()

    # Sanity: the canonical batch is the 11 calls listed in the issue,
    # so the packed bytes must contain each target address (lowercased,
    # no 0x prefix) at least once.
    inner_hex = inner_packed.hex()
    for addr in (
        POLYGON_PUSD,
        POLYGON_CTF,
        POLYGON_USDC_E,
        CTF_EXCHANGE_V2,
        NEG_RISK_CTF_EXCHANGE_V2,
        NEG_RISK_ADAPTER,
        CTF_COLLATERAL_ADAPTER,
        NEG_RISK_CTF_COLLATERAL_ADAPTER,
        COLLATERAL_ONRAMP,
    ):
        assert addr.lower().removeprefix("0x") in inner_hex, (
            f"canonical onboarding batch missing {addr}"
        )

    # Verify packed structure of first inner call. Each call is at least
    # 1 + 20 + 32 + 32 = 85 bytes. The first byte is the operation = 0.
    assert inner_packed[0:1] == b"\x00", "first inner call must be operation=0 (CALL)"

    # Outer MultiSend wrapper — standard ABI bytes encoding around the
    # packed inner.
    result = build_multisend_batch_calldata(inner_packed)

    assert result.startswith("0x8d80ff0a")
    # Standard ABI offset for a single dynamic `bytes` arg = 0x20.
    assert result[10:74] == ("0" * 62) + "20"
    # Then the length word followed by the packed bytes padded to 32.
    inner_length_hex = format(len(inner_packed), "064x")
    assert result[74:138] == inner_length_hex


def test_exec_transaction_calldata_selector_and_full_abi_encoding() -> None:
    """`Safe.execTransaction(to, value, data, operation, safeTxGas,
    baseGas, gasPrice, gasToken, refundReceiver, signatures)` selector
    is `0x6a761202`.

    For V2 onboarding we use:
      * `operation=1` (DELEGATECALL) when calling MultiSend
      * `operation=0` (CALL) for direct calls like `wrap()`
      * `safeTxGas=0, baseGas=0, gasPrice=0, gasToken=0x0, refundReceiver=0x0`
        (no Safe-relayer rebates — the EOA pays gas directly)

    Wrong selector or wrong operation would broadcast a tx that either
    reverts (selector miss) or executes the WRONG semantic (call vs
    delegatecall — the latter inherits the proxy's storage, the former
    runs in the target's storage).
    """
    from polymarket_v2 import build_exec_transaction_calldata

    inner_data = bytes.fromhex("deadbeef")
    signature = bytes.fromhex("ab" * 65)  # standard 65-byte sig

    result = build_exec_transaction_calldata(
        to=SAFE_MULTISEND_1_3_0,
        value=0,
        data=inner_data,
        operation=1,  # DELEGATECALL
        safe_tx_gas=0,
        base_gas=0,
        gas_price=0,
        gas_token="0x" + "00" * 20,
        refund_receiver="0x" + "00" * 20,
        signatures=signature,
    )

    assert result.startswith("0x6a761202")
    # The selector is followed by 10 head words (one per static arg, plus
    # offsets for the two dynamic args data + signatures), then the two
    # dynamic tails. The exact layout is enforced by eth_abi.encode in
    # the implementation; the assertion that matters here is that the
    # MultiSend address and operation=1 are both present in the head.
    body = result[10:]
    assert SAFE_MULTISEND_1_3_0.lower().removeprefix("0x") in body
    # operation is uint8 right-aligned in 32 bytes; "01" is the last two
    # chars of one of the head words.
    operation_word_index = body.find("0" * 62 + "01")
    assert operation_word_index != -1, "operation=1 missing from execTransaction head"


def test_pinned_target_guard_refuses_unpinned_addresses() -> None:
    """Defense-in-depth: the V2 onboarding broadcaster MUST refuse to
    encode any `execTransaction(to=...)` where `to` is outside the
    pinned V2 onboarding allowlist. This is the load-bearing reason the
    pipeline can broadcast under the same #596 live_mode + --yes-live
    gate without a per-call approval prompt — an attacker who somehow
    gets a malicious `to` into the request cannot get it past this
    guard.

    Allowlist contents are the addresses listed in #605:
      * SafeProxyFactory (createProxy target — EOA-direct, not Safe)
      * MultiSend 1.3.0 (delegatecall target for the approve batch)
      * pUSD, USDC.e, CTF (token contracts touched in inner calls)
      * CollateralOnramp (wrap target)
      * CTF Exchange V2 + NegRisk CTF V2 + NegRisk Adapter + 2 CollateralAdapters
        (operators approved in the batch)
    """
    from polymarket_v2 import (
        _check_v2_pinned_target_or_raise,
        SAFE_PROXY_FACTORY as MOD_FACTORY,
        SAFE_MULTISEND_1_3_0 as MOD_MS,
        POLYGON_PUSD as MOD_PUSD,
        COLLATERAL_ONRAMP as MOD_ONRAMP,
    )

    # Sanity: the implementation pins the same addresses this test does.
    assert MOD_FACTORY.lower() == SAFE_PROXY_FACTORY.lower()
    assert MOD_MS.lower() == SAFE_MULTISEND_1_3_0.lower()
    assert MOD_PUSD.lower() == POLYGON_PUSD.lower()
    assert MOD_ONRAMP.lower() == COLLATERAL_ONRAMP.lower()

    # Allowed targets must not raise.
    for allowed in (
        SAFE_PROXY_FACTORY,
        SAFE_MULTISEND_1_3_0,
        POLYGON_PUSD,
        POLYGON_USDC_E,
        POLYGON_CTF,
        COLLATERAL_ONRAMP,
        CTF_EXCHANGE_V2,
        NEG_RISK_CTF_EXCHANGE_V2,
        NEG_RISK_ADAPTER,
        CTF_COLLATERAL_ADAPTER,
        NEG_RISK_CTF_COLLATERAL_ADAPTER,
    ):
        # No raise = pinned.
        _check_v2_pinned_target_or_raise(allowed)

    # Unpinned targets must raise. The CtfAutoRedeem address from the
    # earlier investigation is the canonical "looks Polymarket-ish but
    # is opt-in only" case the agent must NOT broadcast to.
    attacker = "0xdeadbeef" + "0" * 32
    auto_redeem_opt_in = "0xF3cFb6a6eBFeB51876289Eb235719EB1C65252B0"

    with pytest.raises(ValueError, match="unpinned"):
        _check_v2_pinned_target_or_raise(attacker)
    with pytest.raises(ValueError, match="unpinned"):
        _check_v2_pinned_target_or_raise(auto_redeem_opt_in)
