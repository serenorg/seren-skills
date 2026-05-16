"""Issue #596 — auto-approve collapses on the live_mode + --yes-live gate.

The defense surface for Polymarket spender auto-approval is the
encoder, not a separate CLI flag. After #596 there is no
`--auto-approve` flag and no `auto_approve_polymarket_spenders` config
field — when the live trader is active and the polymarket-state
classifier reports `no_approvals`, the bot broadcasts approvals
unconditionally.

These tests cover only the security-critical surface:

  * Pinned-spender drift guard against py-clob-client.
  * Calldata builders REFUSE any spender not in the pinned set.
  * ERC-20 / ERC-1155 calldata encoding is correct.
  * The collapsed orchestrator delegates unconditionally to
    `broadcast_pinned_polymarket_approvals` — there is no skip path.

The on-chain broadcast itself (nonce/gas/sign/send) is exercised by
the functional smoke test against the live runtime — not by unit
tests, because that requires a real key and a real RPC.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Drift guard against py-clob-client. If py-clob-client ever ships a new
# major version with different spender addresses, this test catches it
# at startup before any approval calldata is built.


def test_pinned_spenders_match_py_clob_client_chain_137_both_modes() -> None:
    """Our pinned Polygon-mainnet spender constants must match the
    addresses py-clob-client itself uses for `get_balance_allowance` and
    order submission. If they ever drift, the drift guard catches it
    instead of silently signing approvals to addresses the live CLOB
    won't honor (or worse, to addresses an attacker controls)."""
    from polymarket_live import assert_pinned_spenders_match_py_clob_client

    # No raise = matches. The function returns the resolved set so the
    # caller can log what was verified.
    verified = assert_pinned_spenders_match_py_clob_client(chain_id=137)

    assert "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E".lower() in verified  # CTF Exchange
    assert "0xC5d563A36AE78145C45a50134d48A1215220f80a".lower() in verified  # NegRisk CTF Exchange


# ---------------------------------------------------------------------------
# Pinned-list guard. The single most important test in the file: the
# calldata builders MUST refuse any spender not in the pinned set. This
# is the last line of defense against an attacker who somehow gets a
# malicious spender address into the orchestrator's request — and the
# load-bearing reason #596 can safely collapse the dual opt-in.


def test_calldata_builders_refuse_unpinned_spender() -> None:
    """Both USDC.e.approve() and CT.setApprovalForAll() builders must
    raise on any spender not in the pinned Polymarket spender set."""
    from polymarket_live import (
        build_usdc_approve_calldata,
        build_ct_set_approval_for_all_calldata,
    )

    attacker_address = "0xdeadbeef" + "0" * 32  # not in pinned set

    with pytest.raises(ValueError, match="unpinned"):
        build_usdc_approve_calldata(spender=attacker_address)

    with pytest.raises(ValueError, match="unpinned"):
        build_ct_set_approval_for_all_calldata(spender=attacker_address, approved=True)


# ---------------------------------------------------------------------------
# ERC-20 / ERC-1155 calldata correctness. A wrong selector or wrong
# padding would silently send a tx that does nothing useful (or
# something dangerous). One test per builder covers the full surface.


def test_usdc_approve_calldata_is_selector_plus_padded_spender_plus_max_uint256() -> None:
    """`approve(address,uint256)` selector is 0x095ea7b3. Address is
    left-padded to 32 bytes. Amount is MAX_UINT256 (one-time approval —
    standard pattern, avoids re-approving on every trade)."""
    from polymarket_live import build_usdc_approve_calldata, POLYGON_CTF_EXCHANGE

    result = build_usdc_approve_calldata(spender=POLYGON_CTF_EXCHANGE)

    # 0x + 4-byte selector + 32-byte padded address + 32-byte amount = 138 chars
    assert result.startswith("0x095ea7b3")
    assert len(result) == 138
    # Address segment must match POLYGON_CTF_EXCHANGE left-padded
    assert result[10:74] == "000000000000000000000000" + POLYGON_CTF_EXCHANGE.lower().removeprefix("0x")
    # Amount segment must be MAX_UINT256
    assert result[74:138] == "f" * 64


def test_ct_set_approval_for_all_calldata_is_selector_plus_padded_spender_plus_bool() -> None:
    """`setApprovalForAll(address,bool)` selector is 0xa22cb465. Address
    is left-padded to 32 bytes. Bool is encoded as 32-byte 0x01 for
    true, 0x00 for false."""
    from polymarket_live import (
        build_ct_set_approval_for_all_calldata,
        POLYGON_NEG_RISK_CTF_EXCHANGE,
    )

    result = build_ct_set_approval_for_all_calldata(
        spender=POLYGON_NEG_RISK_CTF_EXCHANGE,
        approved=True,
    )

    assert result.startswith("0xa22cb465")
    assert len(result) == 138
    assert result[10:74] == "000000000000000000000000" + POLYGON_NEG_RISK_CTF_EXCHANGE.lower().removeprefix("0x")
    # Last 32 bytes: bool true = 0x00...01
    assert result[74:138] == "0" * 63 + "1"


# ---------------------------------------------------------------------------
# Issue #596 — collapsed orchestrator. After the collapse there is no
# `config_enabled` / `cli_flag` gate. The orchestrator delegates
# unconditionally to `broadcast_pinned_polymarket_approvals` with the
# wallet + key. This is the contract that the callsite relies on.


def test_auto_approve_orchestrator_delegates_unconditionally_to_broadcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling `auto_approve_missing_polymarket_allowances(wallet_address,
    private_key)` MUST invoke `broadcast_pinned_polymarket_approvals`
    with those exact credentials. No second opt-in flag exists — the
    callsite is the gate (live hedger present == live trading is on)."""
    import polymarket_approvals_broadcast
    from polymarket_live import auto_approve_missing_polymarket_allowances

    captured: dict[str, object] = {}

    def fake_broadcast(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "submitted", "transactions": []}

    monkeypatch.setattr(
        polymarket_approvals_broadcast,
        "broadcast_pinned_polymarket_approvals",
        fake_broadcast,
    )

    result = auto_approve_missing_polymarket_allowances(
        wallet_address="0xAE10914F91E122D73aBFA651c64302EFB8cb9A04",
        private_key="0x" + "1" * 64,
    )

    assert result["status"] == "submitted"
    assert captured["wallet_address"] == "0xAE10914F91E122D73aBFA651c64302EFB8cb9A04"
    assert captured["private_key"] == "0x" + "1" * 64
