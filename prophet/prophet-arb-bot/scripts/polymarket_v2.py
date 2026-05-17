"""Issue #605 — Polymarket V2 onboarding: pure logic.

Polymarket migrated to Exchange V2 + pUSD collateral on 2026-04-28.
Real onboarding now requires:

  1. Deploy a Safe proxy for the EOA via SafeProxyFactory
     `0xaacFeEa03eb1561C4e67d661e40682Bd20E3541b` — selector `0xa1884d2c`,
     CREATE2-deterministic.
  2. Transfer USDC.e from EOA to the proxy address.
  3. Submit an 11-call MultiSend delegatecall batch via
     `Safe.execTransaction` to grant pUSD allowances and CTF
     setApprovalForAll to V2 exchanges + collateral adapters.
  4. Call `CollateralOnramp.wrap(USDC.e, proxy, amount)` via the proxy
     to mint pUSD.

This module holds the pure logic — calldata builders, EIP-712 hashes,
pinned-target allowlist, V2-aware state classifier. The on-chain
probes + orchestrator + broadcast loop live in
`polymarket_v2_broadcast.py`, mirroring the split between
`polymarket_live.py` (pure helpers) and `polymarket_approvals_broadcast.py`
(broadcaster) used for the legacy direct-EOA approve path.

Cross-checked against issue #605 sister tickets (no behavior change):
#536, #538, #542/#589, #592, #596, #600/#601, #602/#603.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from eth_abi import encode as abi_encode
from eth_utils import keccak


# ---------------------------------------------------------------------------
# Pinned V2 onboarding addresses. All checksum-cased to match Polygonscan
# verified-contract labels. Primary-source verified (see #605 description).

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


_V2_PINNED_TARGETS: frozenset[str] = frozenset(
    addr.lower()
    for addr in (
        SAFE_PROXY_FACTORY,
        SAFE_MULTISEND_1_3_0,
        POLYGON_CTF,
        POLYGON_PUSD,
        POLYGON_USDC_E,
        COLLATERAL_ONRAMP,
        CTF_EXCHANGE_V2,
        NEG_RISK_CTF_EXCHANGE_V2,
        NEG_RISK_ADAPTER,
        CTF_COLLATERAL_ADAPTER,
        NEG_RISK_CTF_COLLATERAL_ADAPTER,
    )
)


def _check_v2_pinned_target_or_raise(target: str) -> str:
    """Defense-in-depth: refuse to encode any calldata for a target not
    in the V2 onboarding allowlist. Same pattern as
    `polymarket_live._check_pinned_spender_or_raise` for the legacy
    direct-EOA approve path. Returns the lowercased 40-char address on
    success (caller can use it for ABI encoding)."""
    addr = target.lower().removeprefix("0x")
    if len(addr) != 40:
        raise ValueError(f"target must be 20 bytes hex; got {target!r}")
    if f"0x{addr}" not in _V2_PINNED_TARGETS:
        raise ValueError(
            f"refusing to encode V2 onboarding calldata for unpinned target {target!r}; "
            "the V2 onboarding allowlist is SafeProxyFactory, Safe MultiSend 1.3.0, "
            "pUSD, USDC.e, CTF, CollateralOnramp, CTF Exchange V2, NegRisk CTF V2, "
            "NegRisk Adapter, CtfCollateralAdapter, NegRiskCtfCollateralAdapter"
        )
    return addr


# ---------------------------------------------------------------------------
# ERC-20 / ERC-1155 / Onramp / Safe / MultiSend selectors. Verified
# against canonical sources (function name + arg types → keccak256[:4]).

ERC20_APPROVE_SELECTOR = "0x095ea7b3"  # approve(address,uint256)
ERC20_TRANSFER_SELECTOR = "0xa9059cbb"  # transfer(address,uint256)
ERC1155_SET_APPROVAL_FOR_ALL_SELECTOR = "0xa22cb465"  # setApprovalForAll(address,bool)
COLLATERAL_ONRAMP_WRAP_SELECTOR = "0xea598cb0"  # wrap(uint256) — single-arg wrap variant
SAFE_EXEC_TRANSACTION_SELECTOR = "0x6a761202"  # execTransaction(...)
MULTISEND_SELECTOR = "0x8d80ff0a"  # multiSend(bytes)
CREATE_PROXY_SELECTOR = "0xa1884d2c"  # createProxy(address,uint256,address,(uint8,bytes32,bytes32))

MAX_UINT256_HEX = "f" * 64


def _addr_to_bytes20(addr: str) -> bytes:
    """Normalize a 0x-prefixed hex address to raw 20 bytes."""
    clean = addr.lower().removeprefix("0x")
    if len(clean) != 40:
        raise ValueError(f"address must be 20 bytes hex; got {addr!r}")
    return bytes.fromhex(clean)


def _addr_to_padded_hex(addr: str) -> str:
    """Address left-padded to 32 bytes, returned as 64 hex chars (no
    0x prefix). Used in raw ABI encoding by hand for fixed-shape calls."""
    return _addr_to_bytes20(addr).hex().rjust(64, "0")


# ---------------------------------------------------------------------------
# Inner calldata builders. These produce the bytes that go INSIDE a
# MultiSend batch or a direct `execTransaction`.


def build_pusd_approve_calldata(*, spender: str, amount_hex: str = MAX_UINT256_HEX) -> str:
    """`pUSD.approve(spender, MAX_UINT256)` calldata. Spender must be a
    pinned V2 onboarding target (CTF, V2 exchanges, NegRisk Adapter,
    CollateralAdapters). MAX_UINT256 follows the standard one-time
    approval pattern — avoids burning gas re-approving on every trade."""
    addr = _check_v2_pinned_target_or_raise(spender)
    if len(amount_hex) != 64:
        raise ValueError(f"amount_hex must be 32-byte hex; got {len(amount_hex)} chars")
    return ERC20_APPROVE_SELECTOR + addr.rjust(64, "0") + amount_hex


def build_usdc_e_approve_calldata(*, spender: str, amount_hex: str = MAX_UINT256_HEX) -> str:
    """`USDC.e.approve(spender, MAX_UINT256)` calldata. Only one
    spender is approved on USDC.e during onboarding: the
    CollateralOnramp, so the proxy can wrap USDC.e → pUSD."""
    addr = _check_v2_pinned_target_or_raise(spender)
    if len(amount_hex) != 64:
        raise ValueError(f"amount_hex must be 32-byte hex; got {len(amount_hex)} chars")
    return ERC20_APPROVE_SELECTOR + addr.rjust(64, "0") + amount_hex


def build_ct_set_approval_for_all_calldata(*, operator: str, approved: bool) -> str:
    """`ConditionalTokens.setApprovalForAll(operator, approved)` calldata.
    Operator must be a pinned V2 onboarding target. setApprovalForAll
    is required for the V2 exchanges and CollateralAdapters to move the
    proxy's outcome tokens."""
    addr = _check_v2_pinned_target_or_raise(operator)
    bool_segment = ("0" * 63) + ("1" if approved else "0")
    return ERC1155_SET_APPROVAL_FOR_ALL_SELECTOR + addr.rjust(64, "0") + bool_segment


def build_usdc_e_transfer_calldata(*, recipient: str, amount_raw: int) -> str:
    """`USDC.e.transfer(recipient, amount)` calldata. Recipient is the
    proxy address (step 2 of onboarding); we don't pin-guard recipient
    here because the EOA-direct transfer doesn't pass through
    `execTransaction` — pinned-target guard is for proxy-originated
    calls. Amount is in raw USDC.e units (6 decimals)."""
    addr = recipient.lower().removeprefix("0x")
    if len(addr) != 40:
        raise ValueError(f"recipient must be 20 bytes hex; got {recipient!r}")
    if amount_raw < 0 or amount_raw >= 2**256:
        raise ValueError(f"amount_raw out of uint256 range; got {amount_raw}")
    amount_hex = format(amount_raw, "064x")
    return ERC20_TRANSFER_SELECTOR + addr.rjust(64, "0") + amount_hex


def build_collateral_onramp_wrap_calldata(*, amount_raw: int) -> str:
    """`CollateralOnramp.wrap(amount)` calldata. Pulls `amount` USDC.e
    from msg.sender (the proxy, after the proxy approved CollateralOnramp
    in step 3) and mints the equivalent pUSD to msg.sender."""
    if amount_raw < 0 or amount_raw >= 2**256:
        raise ValueError(f"amount_raw out of uint256 range; got {amount_raw}")
    amount_hex = format(amount_raw, "064x")
    return COLLATERAL_ONRAMP_WRAP_SELECTOR + amount_hex


# ---------------------------------------------------------------------------
# MultiSend batch encoding. Inner format is PACKED (not standard ABI):
#   operation (1) || to (20) || value (32) || data_length (32) || data
# Outer is a standard ABI `bytes` wrapped by selector 0x8d80ff0a.


@dataclass(frozen=True)
class InnerCall:
    """One inner call inside a MultiSend batch. Operation 0 = CALL, 1 =
    DELEGATECALL. For V2 onboarding the OUTER call (proxy ->
    MultiSend) is delegatecall, but every INNER call is operation=0."""

    operation: int
    to: str
    value: int
    data: str  # hex with or without 0x prefix


def _pack_inner_call(call: InnerCall) -> bytes:
    if call.operation not in (0, 1):
        raise ValueError(f"operation must be 0 or 1; got {call.operation}")
    if call.value < 0 or call.value >= 2**256:
        raise ValueError(f"value out of uint256 range; got {call.value}")
    to_bytes = _addr_to_bytes20(call.to)
    data_hex = call.data.removeprefix("0x")
    if len(data_hex) % 2 != 0:
        raise ValueError(f"data must be even-length hex; got {len(data_hex)} chars")
    data_bytes = bytes.fromhex(data_hex)
    return (
        bytes([call.operation])
        + to_bytes
        + call.value.to_bytes(32, "big")
        + len(data_bytes).to_bytes(32, "big")
        + data_bytes
    )


def build_v2_canonical_onboarding_inner_batch() -> bytes:
    """The exact 11-call onboarding batch as observed on Polygonscan for
    freshly-deployed proxies post-V2 migration. Order matters — the
    on-chain trace fixture for tx `0x67407126…` runs these in this
    order, and we mirror it exactly for byte-level reproducibility."""
    calls: list[InnerCall] = [
        # 1. approve(CTF, max) on pUSD
        InnerCall(0, POLYGON_PUSD, 0, build_pusd_approve_calldata(spender=POLYGON_CTF)),
        # 2. approve(CTF Exchange V2, max) on pUSD
        InnerCall(0, POLYGON_PUSD, 0, build_pusd_approve_calldata(spender=CTF_EXCHANGE_V2)),
        # 3. approve(NegRisk CTF V2, max) on pUSD
        InnerCall(0, POLYGON_PUSD, 0, build_pusd_approve_calldata(spender=NEG_RISK_CTF_EXCHANGE_V2)),
        # 4. approve(NegRisk Adapter, max) on pUSD
        InnerCall(0, POLYGON_PUSD, 0, build_pusd_approve_calldata(spender=NEG_RISK_ADAPTER)),
        # 5. approve(CtfCollateralAdapter, max) on pUSD
        InnerCall(0, POLYGON_PUSD, 0, build_pusd_approve_calldata(spender=CTF_COLLATERAL_ADAPTER)),
        # 6. approve(NegRiskCtfCollateralAdapter, max) on pUSD
        InnerCall(0, POLYGON_PUSD, 0, build_pusd_approve_calldata(spender=NEG_RISK_CTF_COLLATERAL_ADAPTER)),
        # 7. setApprovalForAll(CTF Exchange V2, true) on CTF
        InnerCall(0, POLYGON_CTF, 0, build_ct_set_approval_for_all_calldata(operator=CTF_EXCHANGE_V2, approved=True)),
        # 8. setApprovalForAll(NegRisk CTF V2, true) on CTF
        InnerCall(0, POLYGON_CTF, 0, build_ct_set_approval_for_all_calldata(operator=NEG_RISK_CTF_EXCHANGE_V2, approved=True)),
        # 9. approve(CollateralOnramp, max) on USDC.e
        InnerCall(0, POLYGON_USDC_E, 0, build_usdc_e_approve_calldata(spender=COLLATERAL_ONRAMP)),
        # 10. setApprovalForAll(CtfCollateralAdapter, true) on CTF
        InnerCall(0, POLYGON_CTF, 0, build_ct_set_approval_for_all_calldata(operator=CTF_COLLATERAL_ADAPTER, approved=True)),
        # 11. setApprovalForAll(NegRiskCtfCollateralAdapter, true) on CTF
        InnerCall(0, POLYGON_CTF, 0, build_ct_set_approval_for_all_calldata(operator=NEG_RISK_CTF_COLLATERAL_ADAPTER, approved=True)),
    ]
    return b"".join(_pack_inner_call(c) for c in calls)


def build_multisend_batch_calldata(inner_packed: bytes) -> str:
    """Wrap a packed inner batch in `multiSend(bytes)` ABI encoding.
    Returns a 0x-prefixed hex string ready to pass as `data` to
    `Safe.execTransaction(MultiSend, 0, <this>, operation=1, ...)`."""
    encoded_arg = abi_encode(["bytes"], [inner_packed])
    return MULTISEND_SELECTOR + encoded_arg.hex()


# ---------------------------------------------------------------------------
# Safe execTransaction calldata. Single function the proxy exposes for
# every onboarding step after step 1 (deploy) and step 2 (transfer).


def build_exec_transaction_calldata(
    *,
    to: str,
    value: int,
    data: bytes,
    operation: int,
    safe_tx_gas: int,
    base_gas: int,
    gas_price: int,
    gas_token: str,
    refund_receiver: str,
    signatures: bytes,
) -> str:
    """`Safe.execTransaction(to, value, data, operation, safeTxGas,
    baseGas, gasPrice, gasToken, refundReceiver, signatures)` calldata.

    Pinned-target guard fires on `to` because every proxy-originated
    onboarding call must target a known V2 onboarding address. Defense-
    in-depth — even if the orchestrator is somehow handed a malicious
    `to`, the encoder refuses it before signing.

    `data` is the raw bytes payload for the inner call (e.g. the
    MultiSend batch from `build_multisend_batch_calldata`).
    `signatures` is the 65-byte (r || s || v) sig from
    `eth_account.Account.unsafe_sign_hash(safe_tx_hash)` (renamed from the
    legacy `signHash` in eth-account 0.10+; see #609).
    """
    _check_v2_pinned_target_or_raise(to)
    if operation not in (0, 1):
        raise ValueError(f"operation must be 0 (CALL) or 1 (DELEGATECALL); got {operation}")
    encoded_args = abi_encode(
        [
            "address",
            "uint256",
            "bytes",
            "uint8",
            "uint256",
            "uint256",
            "uint256",
            "address",
            "address",
            "bytes",
        ],
        [
            _addr_to_bytes20(to),
            value,
            data,
            operation,
            safe_tx_gas,
            base_gas,
            gas_price,
            _addr_to_bytes20(gas_token),
            _addr_to_bytes20(refund_receiver),
            signatures,
        ],
    )
    return SAFE_EXEC_TRANSACTION_SELECTOR + encoded_args.hex()


# ---------------------------------------------------------------------------
# createProxy calldata. EOA signs the CreateProxy EIP-712 message; the
# (v, r, s) tuple is inlined as the Sig struct arg.


def build_create_proxy_calldata(*, v: int, r: bytes, s: bytes) -> str:
    """`SafeProxyFactory.createProxy(address, uint256, address, Sig)`
    calldata. All three payment fields are zero — Polymarket frontend
    + both confirmed mainnet onboarding txs use zeros, and the bot
    doesn't pay a Safe-relayer rebate. Sig is a struct of (uint8 v,
    bytes32 r, bytes32 s), encoded inline.
    """
    if not (0 <= v <= 255):
        raise ValueError(f"v must be uint8 in [0, 255]; got {v}")
    if len(r) != 32 or len(s) != 32:
        raise ValueError(f"r and s must be 32 bytes; got r={len(r)} s={len(s)}")
    # Hand-encode the 6 fixed-shape 32-byte words to keep the test's
    # byte-exact assertion possible. Order: paymentToken, payment,
    # paymentReceiver, v, r, s.
    zero_word = "0" * 64
    v_word = format(v, "064x")
    return (
        CREATE_PROXY_SELECTOR
        + zero_word  # paymentToken = 0x0
        + zero_word  # payment = 0
        + zero_word  # paymentReceiver = 0x0
        + v_word  # uint8 v right-aligned in 32 bytes
        + r.hex()  # bytes32 r
        + s.hex()  # bytes32 s
    )


# ---------------------------------------------------------------------------
# EIP-712 digests. Two distinct messages — CreateProxy and SafeTx — each
# with its own domain typehash.

_EIP712_DOMAIN_TYPEHASH_WITH_NAME = keccak(
    b"EIP712Domain(string name,uint256 chainId,address verifyingContract)"
)
_EIP712_DOMAIN_TYPEHASH_NO_NAME = keccak(
    b"EIP712Domain(uint256 chainId,address verifyingContract)"
)

_CREATE_PROXY_TYPEHASH = keccak(
    b"CreateProxy(address paymentToken,uint256 payment,address paymentReceiver)"
)
_SAFE_TX_TYPEHASH = keccak(
    b"SafeTx(address to,uint256 value,bytes data,uint8 operation,uint256 safeTxGas,"
    b"uint256 baseGas,uint256 gasPrice,address gasToken,address refundReceiver,uint256 nonce)"
)

_POLYMARKET_PROXY_FACTORY_NAME = b"Polymarket Contract Proxy Factory"


def compute_create_proxy_digest(*, factory: str, chain_id: int = 137) -> bytes:
    """EIP-712 digest for `CreateProxy(0x0, 0, 0x0)` against the
    Polymarket SafeProxyFactory. EOA signs this, then passes (v, r, s)
    to `build_create_proxy_calldata`. Anyone can submit the tx; the
    factory recovers `signer == owner` from the signature, so the EOA
    doesn't need to be the tx sender.
    """
    domain_separator = keccak(
        abi_encode(
            ["bytes32", "bytes32", "uint256", "address"],
            [
                _EIP712_DOMAIN_TYPEHASH_WITH_NAME,
                keccak(_POLYMARKET_PROXY_FACTORY_NAME),
                chain_id,
                _addr_to_bytes20(factory),
            ],
        )
    )
    struct_hash = keccak(
        abi_encode(
            ["bytes32", "address", "uint256", "address"],
            [
                _CREATE_PROXY_TYPEHASH,
                _addr_to_bytes20("0x" + "00" * 20),
                0,
                _addr_to_bytes20("0x" + "00" * 20),
            ],
        )
    )
    return keccak(b"\x19\x01" + domain_separator + struct_hash)


def compute_safe_tx_digest(
    *,
    safe: str,
    to: str,
    value: int,
    data: bytes,
    operation: int,
    safe_tx_gas: int,
    base_gas: int,
    gas_price: int,
    gas_token: str,
    refund_receiver: str,
    nonce: int,
    chain_id: int = 137,
) -> bytes:
    """EIP-712 digest for a Gnosis Safe v1.1.1 SafeTx. EOA signs this
    and the resulting 65-byte signature is passed as `signatures` to
    `Safe.execTransaction`. The on-chain Safe recovers the signer from
    the signature and verifies it matches a registered owner."""
    if operation not in (0, 1):
        raise ValueError(f"operation must be 0 or 1; got {operation}")
    domain_separator = keccak(
        abi_encode(
            ["bytes32", "uint256", "address"],
            [_EIP712_DOMAIN_TYPEHASH_NO_NAME, chain_id, _addr_to_bytes20(safe)],
        )
    )
    struct_hash = keccak(
        abi_encode(
            [
                "bytes32",
                "address",
                "uint256",
                "bytes32",
                "uint8",
                "uint256",
                "uint256",
                "uint256",
                "address",
                "address",
                "uint256",
            ],
            [
                _SAFE_TX_TYPEHASH,
                _addr_to_bytes20(to),
                value,
                keccak(data),
                operation,
                safe_tx_gas,
                base_gas,
                gas_price,
                _addr_to_bytes20(gas_token),
                _addr_to_bytes20(refund_receiver),
                nonce,
            ],
        )
    )
    return keccak(b"\x19\x01" + domain_separator + struct_hash)


# ---------------------------------------------------------------------------
# V2 state classifier. Distinct from V1 because pre-V2 we tracked USDC.e
# on the EOA; post-V2 the CLOB tracks pUSD on the PROXY.

POLYMARKET_V2_STATE_OK = "ok"
POLYMARKET_V2_STATE_NO_PROXY = "no_proxy"
POLYMARKET_V2_STATE_NO_FUNDS_ON_PROXY = "no_funds_on_proxy"
POLYMARKET_V2_STATE_NO_PUSD = "no_pusd"
POLYMARKET_V2_STATE_NO_APPROVALS = "no_approvals"
POLYMARKET_V2_STATE_EOA_NO_BALANCE = "eoa_no_balance"


@dataclass
class PolymarketV2State:
    """Result of `classify_polymarket_v2_state`. `spendable_pusd` is
    what the CLOB sees — use it for funds preflight math, not the raw
    on-chain balances which are informational."""

    kind: str
    spendable_pusd: float
    proxy_has_code: bool
    proxy_usdc_e_balance: Optional[float]
    proxy_pusd_balance: Optional[float]
    eoa_usdc_e_balance: Optional[float]
    remediation: str


_REMEDIATION_V2_OK = "Polymarket reports sufficient spendable pUSD on the proxy."
_REMEDIATION_NO_PROXY = (
    "Polymarket Safe proxy is not yet deployed for this EOA. On the next live "
    "cycle (live_mode=true + --yes-live), the bot will deploy the proxy via "
    "SafeProxyFactory, transfer USDC.e from the EOA, wrap to pUSD via the "
    "CollateralOnramp, and grant the V2 approval batch through Safe MultiSend "
    "in one signed batch. The EOA needs ~0.3 POL on hand for deployment + "
    "approval-batch gas."
)
_REMEDIATION_NO_FUNDS_ON_PROXY = (
    "Polymarket Safe proxy is deployed but holds zero USDC.e and zero pUSD. "
    "On the next live cycle, the bot will transfer USDC.e from the EOA to the "
    "proxy and wrap it to pUSD. If the EOA also holds zero USDC.e, deposit "
    "USDC.e to the EOA first."
)
_REMEDIATION_NO_PUSD = (
    "Polymarket Safe proxy holds USDC.e but no pUSD. On the next live cycle, "
    "the bot will wrap USDC.e to pUSD via the CollateralOnramp through the "
    "proxy."
)
_REMEDIATION_NO_APPROVALS = (
    "Polymarket Safe proxy holds pUSD but has not granted V2 exchange "
    "allowances. On the next live cycle, the bot will submit the 11-call "
    "MultiSend approval batch through the proxy."
)
_REMEDIATION_EOA_NO_BALANCE = (
    "Polymarket Safe proxy is deployed but neither the proxy nor the EOA "
    "holds USDC.e. Deposit USDC.e (Polygon token "
    "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174) to the EOA to fund the "
    "hedge leg."
)


def classify_polymarket_v2_state(
    *,
    clob_balance_pusd: float,
    proxy_has_code: bool,
    proxy_usdc_e_balance: Optional[float],
    proxy_pusd_balance: Optional[float],
    eoa_usdc_e_balance: Optional[float],
) -> PolymarketV2State:
    """V2-aware classifier. Decision tree, in order:

      1. CLOB spendable > 0 → OK (let funds preflight gate sufficiency)
      2. Proxy has no code → no_proxy (need step 1: deploy)
      3. Proxy has zero USDC.e AND zero pUSD AND EOA has zero USDC.e
         → eoa_no_balance (honest deposit required)
      4. Proxy has zero USDC.e AND zero pUSD AND EOA has USDC.e
         → no_funds_on_proxy (need step 2: transfer)
      5. Proxy has USDC.e AND zero pUSD → no_pusd (need step 4: wrap)
      6. Proxy has pUSD but CLOB sees 0 → no_approvals (need step 3:
         MultiSend approve batch)
    """
    spendable = max(0.0, float(clob_balance_pusd))

    def _opt_max(v: Optional[float]) -> Optional[float]:
        return None if v is None else max(0.0, float(v))

    proxy_usdc_e = _opt_max(proxy_usdc_e_balance)
    proxy_pusd = _opt_max(proxy_pusd_balance)
    eoa_usdc_e = _opt_max(eoa_usdc_e_balance)

    common = dict(
        spendable_pusd=spendable,
        proxy_has_code=proxy_has_code,
        proxy_usdc_e_balance=proxy_usdc_e,
        proxy_pusd_balance=proxy_pusd,
        eoa_usdc_e_balance=eoa_usdc_e,
    )

    if spendable > 0.0:
        return PolymarketV2State(kind=POLYMARKET_V2_STATE_OK, remediation=_REMEDIATION_V2_OK, **common)

    if not proxy_has_code:
        return PolymarketV2State(
            kind=POLYMARKET_V2_STATE_NO_PROXY, remediation=_REMEDIATION_NO_PROXY, **common
        )

    proxy_usdc_e_zero = proxy_usdc_e is None or proxy_usdc_e == 0.0
    proxy_pusd_zero = proxy_pusd is None or proxy_pusd == 0.0
    eoa_zero = eoa_usdc_e is None or eoa_usdc_e == 0.0

    if proxy_usdc_e_zero and proxy_pusd_zero:
        if eoa_zero:
            return PolymarketV2State(
                kind=POLYMARKET_V2_STATE_EOA_NO_BALANCE,
                remediation=_REMEDIATION_EOA_NO_BALANCE,
                **common,
            )
        return PolymarketV2State(
            kind=POLYMARKET_V2_STATE_NO_FUNDS_ON_PROXY,
            remediation=_REMEDIATION_NO_FUNDS_ON_PROXY,
            **common,
        )

    if not proxy_usdc_e_zero and proxy_pusd_zero:
        return PolymarketV2State(
            kind=POLYMARKET_V2_STATE_NO_PUSD, remediation=_REMEDIATION_NO_PUSD, **common
        )

    # Proxy holds pUSD but CLOB still sees zero — missing approvals.
    return PolymarketV2State(
        kind=POLYMARKET_V2_STATE_NO_APPROVALS, remediation=_REMEDIATION_NO_APPROVALS, **common
    )


__all__ = [
    # addresses
    "SAFE_PROXY_FACTORY",
    "SAFE_MULTISEND_1_3_0",
    "POLYGON_CTF",
    "POLYGON_PUSD",
    "POLYGON_USDC_E",
    "COLLATERAL_ONRAMP",
    "CTF_EXCHANGE_V2",
    "NEG_RISK_CTF_EXCHANGE_V2",
    "NEG_RISK_ADAPTER",
    "CTF_COLLATERAL_ADAPTER",
    "NEG_RISK_CTF_COLLATERAL_ADAPTER",
    # guards
    "_check_v2_pinned_target_or_raise",
    # calldata builders
    "build_create_proxy_calldata",
    "build_exec_transaction_calldata",
    "build_multisend_batch_calldata",
    "build_v2_canonical_onboarding_inner_batch",
    "build_pusd_approve_calldata",
    "build_usdc_e_approve_calldata",
    "build_usdc_e_transfer_calldata",
    "build_collateral_onramp_wrap_calldata",
    "build_ct_set_approval_for_all_calldata",
    "InnerCall",
    # EIP-712
    "compute_create_proxy_digest",
    "compute_safe_tx_digest",
    # selectors (exported for diagnostic logging)
    "CREATE_PROXY_SELECTOR",
    "SAFE_EXEC_TRANSACTION_SELECTOR",
    "MULTISEND_SELECTOR",
    "COLLATERAL_ONRAMP_WRAP_SELECTOR",
    "ERC20_APPROVE_SELECTOR",
    "ERC20_TRANSFER_SELECTOR",
    "ERC1155_SET_APPROVAL_FOR_ALL_SELECTOR",
    # state
    "POLYMARKET_V2_STATE_OK",
    "POLYMARKET_V2_STATE_NO_PROXY",
    "POLYMARKET_V2_STATE_NO_FUNDS_ON_PROXY",
    "POLYMARKET_V2_STATE_NO_PUSD",
    "POLYMARKET_V2_STATE_NO_APPROVALS",
    "POLYMARKET_V2_STATE_EOA_NO_BALANCE",
    "PolymarketV2State",
    "classify_polymarket_v2_state",
]
