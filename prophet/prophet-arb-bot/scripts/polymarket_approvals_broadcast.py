"""Issue #592 phase 2 — on-chain broadcast of Polymarket spender approvals.

Kept separate from `polymarket_live.py` so the eth-account dependency
is only imported when the broadcast actually runs (i.e. when the live
trader is wired in and the polymarket-state classifier reported
`no_approvals` for the configured wallet).

The pinned-list guard lives in `polymarket_live.build_*_calldata` and
in `_check_pinned_spender_or_raise`. Issue #596 collapsed the
historical dual opt-in (config flag + CLI flag) into the live_mode +
--yes-live gate the trade-signing path already enforces — defense-in-
depth stays in the encoder, where it belongs.

The broadcast itself is idempotent: it queries the current allowance
and `isApprovedForAll` per pinned spender and skips broadcasts where
the on-chain state already shows approval. Re-running on every cycle
is a constant-cost no-op once the wallet is approved.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.request import Request, urlopen

from polymarket_live import (
    POLYGON_CONDITIONAL_TOKENS,
    POLYGON_USDC_E,
    _PINNED_POLYMARKET_SPENDERS,
    build_ct_set_approval_for_all_calldata,
    build_usdc_approve_calldata,
    call_publisher_json,
    safe_int,
)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_CHAIN_ID = 137
RECEIPT_POLL_INTERVAL_SECONDS = 3.0
RECEIPT_POLL_MAX_ATTEMPTS = 40  # 40 * 3s = 2 minutes worst case

USDC_ALLOWANCE_THRESHOLD = 10**24  # 1e18 USDC.e; effectively "still unlimited"


def _seren_polygon_rpc(
    *,
    method: str,
    params: list[Any],
    seren_publisher: str,
    timeout_seconds: float,
) -> Any:
    """Send a JSON-RPC request through seren-polygon and unwrap to the
    raw `result` field. Returns ``None`` on transport errors or RPC
    errors (caller decides how to handle).
    """
    payload = call_publisher_json(
        publisher=seren_publisher,
        method="POST",
        path="",
        body={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout_seconds=timeout_seconds,
    )
    cursor: Any = payload
    for key in ("data", "body"):
        if isinstance(cursor, dict) and key in cursor:
            cursor = cursor[key]
    if not isinstance(cursor, dict):
        return None
    if cursor.get("error") not in (None, {}):
        return None
    return cursor.get("result")


def _read_usdc_allowance(
    *, wallet: str, spender: str, seren_publisher: str, timeout_seconds: float
) -> int:
    wallet_padded = wallet.lower().removeprefix("0x").rjust(64, "0")
    spender_padded = spender.lower().removeprefix("0x").rjust(64, "0")
    calldata = "0xdd62ed3e" + wallet_padded + spender_padded
    result = _seren_polygon_rpc(
        method="eth_call",
        params=[{"to": POLYGON_USDC_E, "data": calldata}, "latest"],
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if not result or result == "0x":
        return 0
    try:
        return int(result, 16)
    except (TypeError, ValueError):
        return 0


def _read_ct_approval(
    *, wallet: str, spender: str, seren_publisher: str, timeout_seconds: float
) -> bool:
    wallet_padded = wallet.lower().removeprefix("0x").rjust(64, "0")
    spender_padded = spender.lower().removeprefix("0x").rjust(64, "0")
    calldata = "0xe985e9c5" + wallet_padded + spender_padded
    result = _seren_polygon_rpc(
        method="eth_call",
        params=[{"to": POLYGON_CONDITIONAL_TOKENS, "data": calldata}, "latest"],
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if not result or result == "0x":
        return False
    try:
        return int(result, 16) > 0
    except (TypeError, ValueError):
        return False


def _build_and_send_tx(
    *,
    to: str,
    data: str,
    wallet_address: str,
    private_key: str,
    seren_publisher: str,
    timeout_seconds: float,
    chain_id: int = DEFAULT_CHAIN_ID,
) -> dict[str, Any]:
    """Build, sign, and broadcast a single approval transaction.
    Returns `{"tx_hash": "...", "status": "submitted"}` on success or
    `{"status": "failed", "error": "..."}` on any error.
    """
    try:
        from eth_account import Account
    except ImportError as exc:
        return {"status": "failed", "error": f"eth-account import: {exc}"}

    # Fetch nonce (pending so back-to-back txs don't collide).
    nonce_hex = _seren_polygon_rpc(
        method="eth_getTransactionCount",
        params=[wallet_address, "pending"],
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if nonce_hex is None:
        return {"status": "failed", "error": "nonce_fetch_failed"}
    nonce = int(nonce_hex, 16)

    # EIP-1559 fees: base fee from latest block + 2 gwei tip.
    block = _seren_polygon_rpc(
        method="eth_getBlockByNumber",
        params=["latest", False],
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if not isinstance(block, dict) or "baseFeePerGas" not in block:
        return {"status": "failed", "error": "base_fee_fetch_failed"}
    base_fee = int(block["baseFeePerGas"], 16)
    max_priority = 2 * 10**9  # 2 gwei — Polygon minimum
    max_fee = base_fee * 2 + max_priority

    # Estimate gas with a small safety buffer.
    gas_hex = _seren_polygon_rpc(
        method="eth_estimateGas",
        params=[{"from": wallet_address, "to": to, "data": data, "value": "0x0"}],
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if gas_hex is None:
        return {"status": "failed", "error": "gas_estimate_failed"}
    gas_limit = int(int(gas_hex, 16) * 1.2)

    tx = {
        "type": 2,
        "chainId": chain_id,
        "nonce": nonce,
        "to": to,
        "value": 0,
        "data": data,
        "gas": gas_limit,
        "maxPriorityFeePerGas": max_priority,
        "maxFeePerGas": max_fee,
    }
    signed = Account.sign_transaction(tx, private_key)
    raw_hex = "0x" + signed.raw_transaction.hex()

    tx_hash = _seren_polygon_rpc(
        method="eth_sendRawTransaction",
        params=[raw_hex],
        seren_publisher=seren_publisher,
        timeout_seconds=timeout_seconds,
    )
    if not tx_hash:
        return {"status": "failed", "error": "broadcast_failed"}

    # Poll for receipt.
    for _ in range(RECEIPT_POLL_MAX_ATTEMPTS):
        receipt = _seren_polygon_rpc(
            method="eth_getTransactionReceipt",
            params=[tx_hash],
            seren_publisher=seren_publisher,
            timeout_seconds=timeout_seconds,
        )
        if isinstance(receipt, dict):
            status = receipt.get("status")
            if status in ("0x1", 1, "0x01"):
                return {"status": "confirmed", "tx_hash": tx_hash}
            if status in ("0x0", 0, "0x00"):
                return {"status": "reverted", "tx_hash": tx_hash}
        time.sleep(RECEIPT_POLL_INTERVAL_SECONDS)

    return {"status": "pending", "tx_hash": tx_hash}


def broadcast_pinned_polymarket_approvals(
    *,
    wallet_address: str,
    private_key: str,
    seren_publisher: str = "seren-polygon",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Iterate every pinned Polymarket spender, broadcast USDC.e
    `approve(MAX_UINT256)` + CT `setApprovalForAll(true)` where the
    on-chain state shows they're missing. Idempotent — skips spenders
    that already have approval.

    The pinned-list guard inside `build_*_calldata` is the
    last-line-of-defense check; this function additionally iterates
    only `_PINNED_POLYMARKET_SPENDERS`, so an attacker can't sneak an
    address in via a parameter.
    """
    if not wallet_address or not private_key:
        return {"status": "failed", "error": "missing_credentials", "transactions": []}

    transactions: list[dict[str, Any]] = []

    for spender in sorted(_PINNED_POLYMARKET_SPENDERS):
        # USDC.e allowance leg.
        current_allowance = _read_usdc_allowance(
            wallet=wallet_address,
            spender=spender,
            seren_publisher=seren_publisher,
            timeout_seconds=timeout_seconds,
        )
        if current_allowance >= USDC_ALLOWANCE_THRESHOLD:
            transactions.append(
                {"spender": spender, "leg": "usdc_approve", "status": "skipped_already_approved"}
            )
        else:
            calldata = build_usdc_approve_calldata(spender=spender)
            result = _build_and_send_tx(
                to=POLYGON_USDC_E,
                data=calldata,
                wallet_address=wallet_address,
                private_key=private_key,
                seren_publisher=seren_publisher,
                timeout_seconds=timeout_seconds,
            )
            result["spender"] = spender
            result["leg"] = "usdc_approve"
            transactions.append(result)

        # CT setApprovalForAll leg.
        already_approved = _read_ct_approval(
            wallet=wallet_address,
            spender=spender,
            seren_publisher=seren_publisher,
            timeout_seconds=timeout_seconds,
        )
        if already_approved:
            transactions.append(
                {"spender": spender, "leg": "ct_set_approval", "status": "skipped_already_approved"}
            )
        else:
            calldata = build_ct_set_approval_for_all_calldata(spender=spender, approved=True)
            result = _build_and_send_tx(
                to=POLYGON_CONDITIONAL_TOKENS,
                data=calldata,
                wallet_address=wallet_address,
                private_key=private_key,
                seren_publisher=seren_publisher,
                timeout_seconds=timeout_seconds,
            )
            result["spender"] = spender
            result["leg"] = "ct_set_approval"
            transactions.append(result)

    failed = [t for t in transactions if t.get("status") in ("failed", "reverted")]
    overall = "failed" if failed else "submitted"
    return {"status": overall, "transactions": transactions}
