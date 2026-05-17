"""Issue #605 — Polymarket V2 onboarding: on-chain probes + orchestrator.

Pure logic lives in `polymarket_v2.py`. This module is the broadcast
seam — same architectural split as `polymarket_live.py` (helpers) vs
`polymarket_approvals_broadcast.py` (broadcaster) for the legacy
direct-EOA approve path.

The orchestrator is idempotent. Every cycle, for a wallet that has
already completed onboarding, it makes ~6 read-only RPC calls (one
`eth_getCode`, one `balanceOf(USDC.e)` on proxy, one `balanceOf(pUSD)`
on proxy, plus one read to derive the proxy address from the factory)
and returns `skipped_already_onboarded` without signing anything.

Live gate: same as #596 — the orchestrator is invoked from
`_annotate_polymarket_state` only when the live hedger is wired in,
which already requires `live_mode=true` + `--yes-live`. No new opt-in
flag.

Pinned-target defense-in-depth lives in `polymarket_v2._check_v2_pinned_target_or_raise`:
every `execTransaction` calldata builder rejects unpinned `to`
addresses before signing.
"""

from __future__ import annotations

from typing import Any, Optional

from polymarket_v2 import (
    COLLATERAL_ONRAMP,
    POLYGON_PUSD,
    POLYGON_USDC_E,
    SAFE_MULTISEND_1_3_0,
    SAFE_PROXY_FACTORY,
    build_collateral_onramp_wrap_calldata,
    build_create_proxy_calldata,
    build_exec_transaction_calldata,
    build_multisend_batch_calldata,
    build_usdc_e_transfer_calldata,
    build_v2_canonical_onboarding_inner_batch,
    compute_create_proxy_digest,
    compute_safe_tx_digest,
)
from polymarket_approvals_broadcast import (
    DEFAULT_CHAIN_ID,
    DEFAULT_TIMEOUT_SECONDS,
    _build_and_send_tx,
    _seren_polygon_rpc,
)

# Allowance threshold reused from the legacy broadcaster — once allowance
# exceeds 1e24, treat it as "still effectively unlimited" so cycles
# don't burn gas re-approving after partial fills.
PUSD_ALLOWANCE_THRESHOLD = 10**24

# Selectors used here directly so we can call view functions on
# SafeProxyFactory and SafeProxy without round-tripping through
# polymarket_v2.
_COMPUTE_PROXY_ADDRESS_SELECTOR = "0xd600539a"  # computeProxyAddress(address) — #607: keccak-verified canonical selector
_NONCE_SELECTOR = "0xaffed0e0"  # nonce()  (Gnosis Safe)
_ERC20_BALANCE_OF_SELECTOR = "0x70a08231"  # balanceOf(address)
_ERC20_ALLOWANCE_SELECTOR = "0xdd62ed3e"  # allowance(address,address)


# ---------------------------------------------------------------------------
# Read-only on-chain probes. All return ``None`` on transport failure so
# the orchestrator can treat the cycle as "state unknown, skip safely"
# rather than mis-classifying.


def _pad_address(addr: str) -> str:
    return addr.lower().removeprefix("0x").rjust(64, "0")


def fetch_proxy_address_for_eoa(
    *,
    eoa_address: str,
    factory: str = SAFE_PROXY_FACTORY,
    seren_publisher: str = "seren-polygon",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Optional[str]:
    """Read the CREATE2 proxy address the factory would deploy for
    `eoa_address`. Calls `factory.computeProxyAddress(eoa)` view — no
    gas, no signing. Returns the proxy address in EIP-55 checksum form
    (0x-prefixed) or ``None`` on RPC error.

    #613: must be checksum-cased. eth-account 0.10+
    `DynamicFeeTransaction.assert_valid_fields` raises
    `TypeError: Transaction had invalid fields: {'to': ...}` if a non-
    checksum string address is passed as `to` to `Account.sign_transaction`,
    which crashed steps 3 + 4 of the V2 onboarding orchestrator every
    cycle after the proxy was deployed. Normalizing at this seam means
    every downstream consumer (SafeTx digest, exec-transaction calldata,
    transaction `to`) gets the canonical form.
    """
    from eth_utils import to_checksum_address

    calldata = _COMPUTE_PROXY_ADDRESS_SELECTOR + _pad_address(eoa_address)
    result = _seren_polygon_rpc(
        method="eth_call",
        params=[{"to": factory, "data": calldata}, "latest"],
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if not result or result == "0x" or len(result) < 66:
        return None
    # Address is the last 20 bytes of the 32-byte return word.
    return to_checksum_address("0x" + result.removeprefix("0x")[-40:])


def fetch_eth_get_code(
    address: str,
    *,
    seren_publisher: str = "seren-polygon",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Optional[str]:
    """`eth_getCode(address)` — used to determine whether a proxy is
    already deployed. Returns ``"0x"`` for empty / no code. Returns
    ``None`` only on transport failure (caller treats as state-unknown).
    """
    result = _seren_polygon_rpc(
        method="eth_getCode",
        params=[address, "latest"],
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if result is None:
        return None
    return result


def fetch_erc20_balance_raw(
    *,
    token: str,
    owner: str,
    seren_publisher: str = "seren-polygon",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Optional[int]:
    """`token.balanceOf(owner)` raw integer (no decimal scaling)."""
    calldata = _ERC20_BALANCE_OF_SELECTOR + _pad_address(owner)
    result = _seren_polygon_rpc(
        method="eth_call",
        params=[{"to": token, "data": calldata}, "latest"],
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if not result or result == "0x":
        return None
    try:
        return int(result, 16)
    except (TypeError, ValueError):
        return None


def fetch_erc20_allowance_raw(
    *,
    token: str,
    owner: str,
    spender: str,
    seren_publisher: str = "seren-polygon",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Optional[int]:
    """`token.allowance(owner, spender)` raw integer."""
    calldata = _ERC20_ALLOWANCE_SELECTOR + _pad_address(owner) + _pad_address(spender)
    result = _seren_polygon_rpc(
        method="eth_call",
        params=[{"to": token, "data": calldata}, "latest"],
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if not result or result == "0x":
        return None
    try:
        return int(result, 16)
    except (TypeError, ValueError):
        return None


def fetch_safe_nonce(
    *,
    safe: str,
    seren_publisher: str = "seren-polygon",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Optional[int]:
    """`safe.nonce()` — required for SafeTx EIP-712 signing."""
    result = _seren_polygon_rpc(
        method="eth_call",
        params=[{"to": safe, "data": _NONCE_SELECTOR}, "latest"],
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if not result or result == "0x":
        return None
    try:
        return int(result, 16)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Broadcast helpers. Wrap `_build_and_send_tx` from the legacy
# broadcaster (reuses EIP-1559 fee resolution, nonce fetch, gas estimate,
# receipt polling, and #602 priority-fee floor).


def _broadcast_create_proxy(
    *,
    eoa_address: str,
    eoa_private_key: str,
    factory: str,
    seren_publisher: str,
    timeout_seconds: float,
    chain_id: int = DEFAULT_CHAIN_ID,
) -> dict[str, Any]:
    """Sign the CreateProxy EIP-712 message with `eoa_private_key`,
    then broadcast `factory.createProxy(0x0, 0, 0x0, sig)` from the EOA.
    Returns the same status/tx_hash envelope as `_build_and_send_tx`.
    """
    try:
        from eth_account import Account
    except ImportError as exc:
        return {"status": "failed", "error": f"eth-account import: {exc}"}

    digest = compute_create_proxy_digest(factory=factory, chain_id=chain_id)
    # #609: Account.signHash was removed in eth-account 0.10+; use unsafe_sign_hash
    # over the pre-computed EIP-712 digest. Same (v, r, s) shape downstream.
    signed = Account.unsafe_sign_hash(digest, private_key=eoa_private_key)
    calldata = build_create_proxy_calldata(
        v=signed.v,
        r=signed.r.to_bytes(32, "big"),
        s=signed.s.to_bytes(32, "big"),
    )
    return _build_and_send_tx(
        to=factory,
        data=calldata,
        wallet_address=eoa_address,
        private_key=eoa_private_key,
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
        chain_id=chain_id,
    )


def _broadcast_usdc_e_transfer(
    *,
    eoa_address: str,
    eoa_private_key: str,
    proxy_address: str,
    amount_raw: int,
    seren_publisher: str,
    timeout_seconds: float,
    chain_id: int = DEFAULT_CHAIN_ID,
) -> dict[str, Any]:
    """`USDC.e.transfer(proxy, amount_raw)` from the EOA. No Safe
    involvement — this is a plain ERC-20 transfer."""
    calldata = build_usdc_e_transfer_calldata(
        recipient=proxy_address, amount_raw=amount_raw
    )
    return _build_and_send_tx(
        to=POLYGON_USDC_E,
        data=calldata,
        wallet_address=eoa_address,
        private_key=eoa_private_key,
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
        chain_id=chain_id,
    )


def _broadcast_safe_exec_transaction(
    *,
    safe: str,
    inner_to: str,
    inner_value: int,
    inner_data: bytes,
    inner_operation: int,
    nonce: int,
    eoa_address: str,
    eoa_private_key: str,
    seren_publisher: str,
    timeout_seconds: float,
    chain_id: int = DEFAULT_CHAIN_ID,
) -> dict[str, Any]:
    """Sign the SafeTx EIP-712 message and broadcast
    `safe.execTransaction(inner_to, 0, inner_data, inner_operation, 0, 0, 0, 0x0, 0x0, sig)`
    from the EOA. The Safe recovers `signer == owner` from the sig and
    validates the call."""
    try:
        from eth_account import Account
    except ImportError as exc:
        return {"status": "failed", "error": f"eth-account import: {exc}"}

    zero_addr = "0x" + "00" * 20
    digest = compute_safe_tx_digest(
        safe=safe,
        to=inner_to,
        value=inner_value,
        data=inner_data,
        operation=inner_operation,
        safe_tx_gas=0,
        base_gas=0,
        gas_price=0,
        gas_token=zero_addr,
        refund_receiver=zero_addr,
        nonce=nonce,
        chain_id=chain_id,
    )
    # #609: Account.signHash was removed in eth-account 0.10+; use unsafe_sign_hash
    # over the pre-computed SafeTx digest. Same (v, r, s) shape downstream.
    signed = Account.unsafe_sign_hash(digest, private_key=eoa_private_key)
    # Safe expects signatures as `r || s || v` packed bytes (NOT r || s || (v+4)
    # — that's only for the eth_sign variant; for EOA contract sig the v is
    # the standard 27/28).
    sig_bytes = (
        signed.r.to_bytes(32, "big")
        + signed.s.to_bytes(32, "big")
        + bytes([signed.v])
    )
    calldata = build_exec_transaction_calldata(
        to=inner_to,
        value=inner_value,
        data=inner_data,
        operation=inner_operation,
        safe_tx_gas=0,
        base_gas=0,
        gas_price=0,
        gas_token=zero_addr,
        refund_receiver=zero_addr,
        signatures=sig_bytes,
    )
    return _build_and_send_tx(
        to=safe,
        data=calldata,
        wallet_address=eoa_address,
        private_key=eoa_private_key,
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
        chain_id=chain_id,
    )


# ---------------------------------------------------------------------------
# Orchestrator. Four-step idempotent pipeline.


def _pusd_allowance_complete(
    *,
    proxy: str,
    spender: str,
    seren_publisher: str,
    timeout_seconds: float,
) -> bool:
    """Return True if proxy has approved spender on pUSD past the
    'effectively unlimited' threshold."""
    allowance = fetch_erc20_allowance_raw(
        token=POLYGON_PUSD,
        owner=proxy,
        spender=spender,
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if allowance is None:
        # RPC failure — be conservative, treat as "not yet approved" so
        # the broadcast retries. Per-spender skip in the legacy
        # broadcaster behaves the same way.
        return False
    return allowance >= PUSD_ALLOWANCE_THRESHOLD


def onboard_polymarket_v2(
    *,
    eoa_address: str,
    eoa_private_key: str,
    target_usdc_e_raw: int,
    factory: str = SAFE_PROXY_FACTORY,
    seren_publisher: str = "seren-polygon",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    chain_id: int = DEFAULT_CHAIN_ID,
) -> dict[str, Any]:
    """Idempotent 4-step V2 onboarding orchestrator. Skips any step
    where on-chain state already shows completion.

    Returns ``{"status": "...", "proxy_address": "...",
    "transactions": [...]}`` where status is one of:
      * `skipped_already_onboarded` — proxy deployed, funds present,
        pUSD allowance to canonical sentinel spender is at threshold
      * `onboarded` — pipeline ran through to completion this cycle
      * `failed` — at least one broadcast leg failed
      * `proxy_address_unavailable` — couldn't read the proxy address
        from the factory (RPC error); cycle should retry next tick
    """
    if not eoa_address or not eoa_private_key:
        return {
            "status": "failed",
            "error": "missing_credentials",
            "transactions": [],
        }

    transactions: list[dict[str, Any]] = []

    # Pre-flight: derive the proxy address for the EOA. This is the
    # CREATE2 address the factory will deploy — same address pre- and
    # post-deployment.
    proxy_address = fetch_proxy_address_for_eoa(
        eoa_address=eoa_address,
        factory=factory,
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if not proxy_address:
        return {
            "status": "proxy_address_unavailable",
            "proxy_address": None,
            "transactions": transactions,
        }

    # Step 1: deploy proxy if missing.
    proxy_code = fetch_eth_get_code(
        proxy_address,
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    proxy_has_code = bool(proxy_code) and proxy_code != "0x"
    if not proxy_has_code:
        deploy_result = _broadcast_create_proxy(
            eoa_address=eoa_address,
            eoa_private_key=eoa_private_key,
            factory=factory,
            seren_publisher=seren_publisher,
            timeout_seconds=timeout_seconds,
            chain_id=chain_id,
        )
        deploy_result["leg"] = "create_proxy"
        transactions.append(deploy_result)
        if deploy_result.get("status") in ("failed", "reverted"):
            return {
                "status": "failed",
                "proxy_address": proxy_address,
                "transactions": transactions,
            }
        # After deploy, defer the rest to the next cycle. The proxy is
        # now on-chain but its nonce/state may not have settled into
        # the indexer; idempotency makes this safe.
        return {
            "status": "onboarded",
            "proxy_address": proxy_address,
            "transactions": transactions,
        }

    # Step 2: transfer USDC.e from EOA to proxy if proxy doesn't have
    # enough USDC.e (or enough pUSD already).
    proxy_pusd = fetch_erc20_balance_raw(
        token=POLYGON_PUSD,
        owner=proxy_address,
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    ) or 0
    proxy_usdc_e = fetch_erc20_balance_raw(
        token=POLYGON_USDC_E,
        owner=proxy_address,
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    ) or 0
    available_collateral_raw = proxy_pusd + proxy_usdc_e

    if available_collateral_raw < target_usdc_e_raw:
        deficit = target_usdc_e_raw - available_collateral_raw
        eoa_usdc_e = fetch_erc20_balance_raw(
            token=POLYGON_USDC_E,
            owner=eoa_address,
            seren_publisher=seren_publisher,
            timeout_seconds=timeout_seconds,
        ) or 0
        if eoa_usdc_e <= 0:
            # Nothing to transfer — caller must deposit USDC.e to the EOA.
            return {
                "status": "failed",
                "error": "eoa_no_usdc_e",
                "proxy_address": proxy_address,
                "transactions": transactions,
            }
        transfer_amount = min(deficit, eoa_usdc_e)
        transfer_result = _broadcast_usdc_e_transfer(
            eoa_address=eoa_address,
            eoa_private_key=eoa_private_key,
            proxy_address=proxy_address,
            amount_raw=transfer_amount,
            seren_publisher=seren_publisher,
            timeout_seconds=timeout_seconds,
            chain_id=chain_id,
        )
        transfer_result["leg"] = "usdc_e_transfer"
        transactions.append(transfer_result)
        if transfer_result.get("status") in ("failed", "reverted"):
            return {
                "status": "failed",
                "proxy_address": proxy_address,
                "transactions": transactions,
            }
        # Refresh the proxy USDC.e balance so step 4 sees the new amount
        # without a second RPC round-trip later in the same orchestrator
        # invocation. Receipt is already confirmed by _build_and_send_tx.
        proxy_usdc_e += transfer_amount

    # Step 3: approve batch via MultiSend if approvals are missing.
    # We probe one sentinel spender (CTF Exchange V2) — if pUSD allowance
    # to that one is at MAX, treat the whole batch as done. The 11-call
    # batch is atomic on-chain, so partial-completion is not a real state.
    from polymarket_v2 import CTF_EXCHANGE_V2

    approvals_complete = _pusd_allowance_complete(
        proxy=proxy_address,
        spender=CTF_EXCHANGE_V2,
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if not approvals_complete:
        nonce = fetch_safe_nonce(
            safe=proxy_address,
            seren_publisher=seren_publisher,
            timeout_seconds=timeout_seconds,
        )
        if nonce is None:
            return {
                "status": "failed",
                "error": "safe_nonce_unavailable",
                "proxy_address": proxy_address,
                "transactions": transactions,
            }
        inner_batch = build_v2_canonical_onboarding_inner_batch()
        # `build_multisend_batch_calldata` returns a 0x-prefixed hex
        # string; pass the raw bytes through `_broadcast_safe_exec_transaction`
        # so the SafeTx digest hashes the same `inner_data`.
        multisend_calldata_hex = build_multisend_batch_calldata(inner_batch)
        multisend_calldata_bytes = bytes.fromhex(
            multisend_calldata_hex.removeprefix("0x")
        )
        approve_result = _broadcast_safe_exec_transaction(
            safe=proxy_address,
            inner_to=SAFE_MULTISEND_1_3_0,
            inner_value=0,
            inner_data=multisend_calldata_bytes,
            inner_operation=1,  # DELEGATECALL — MultiSend runs inner calls in proxy storage
            nonce=nonce,
            eoa_address=eoa_address,
            eoa_private_key=eoa_private_key,
            seren_publisher=seren_publisher,
            timeout_seconds=timeout_seconds,
            chain_id=chain_id,
        )
        approve_result["leg"] = "multisend_approve_batch"
        transactions.append(approve_result)
        if approve_result.get("status") in ("failed", "reverted"):
            return {
                "status": "failed",
                "proxy_address": proxy_address,
                "transactions": transactions,
            }

    # Step 4: wrap USDC.e -> pUSD if the proxy has USDC.e but no pUSD.
    # Re-read pUSD balance because step 3 may have settled the approve
    # batch but not changed pUSD; we still need to wrap whatever USDC.e
    # is on the proxy.
    if proxy_usdc_e > 0:
        proxy_pusd_after_approve = fetch_erc20_balance_raw(
            token=POLYGON_PUSD,
            owner=proxy_address,
            seren_publisher=seren_publisher,
            timeout_seconds=timeout_seconds,
        ) or 0
        if proxy_pusd_after_approve < target_usdc_e_raw:
            nonce = fetch_safe_nonce(
                safe=proxy_address,
                seren_publisher=seren_publisher,
                timeout_seconds=timeout_seconds,
            )
            if nonce is None:
                return {
                    "status": "failed",
                    "error": "safe_nonce_unavailable",
                    "proxy_address": proxy_address,
                    "transactions": transactions,
                }
            wrap_calldata_hex = build_collateral_onramp_wrap_calldata(
                amount_raw=proxy_usdc_e
            )
            wrap_calldata_bytes = bytes.fromhex(wrap_calldata_hex.removeprefix("0x"))
            wrap_result = _broadcast_safe_exec_transaction(
                safe=proxy_address,
                inner_to=COLLATERAL_ONRAMP,
                inner_value=0,
                inner_data=wrap_calldata_bytes,
                inner_operation=0,  # CALL — wrap runs in CollateralOnramp storage
                nonce=nonce,
                eoa_address=eoa_address,
                eoa_private_key=eoa_private_key,
                seren_publisher=seren_publisher,
                timeout_seconds=timeout_seconds,
                chain_id=chain_id,
            )
            wrap_result["leg"] = "collateral_onramp_wrap"
            transactions.append(wrap_result)
            if wrap_result.get("status") in ("failed", "reverted"):
                return {
                    "status": "failed",
                    "proxy_address": proxy_address,
                    "transactions": transactions,
                }

    if not transactions:
        return {
            "status": "skipped_already_onboarded",
            "proxy_address": proxy_address,
            "transactions": [],
        }
    return {
        "status": "onboarded",
        "proxy_address": proxy_address,
        "transactions": transactions,
    }


__all__ = [
    "fetch_proxy_address_for_eoa",
    "fetch_eth_get_code",
    "fetch_erc20_balance_raw",
    "fetch_erc20_allowance_raw",
    "fetch_safe_nonce",
    "onboard_polymarket_v2",
]
