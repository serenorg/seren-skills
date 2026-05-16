"""Issue #600 — extend `_PINNED_POLYMARKET_SPENDERS` to include CTF Exchange v2.

Polymarket migrated to v2 CTF Exchange addresses (`0xE111...` standard and
`0xe222...` neg-risk). py-clob-client v0.34 still returns the v1 addresses
in `get_contract_config`, so the bot's pinned set — derived from py-clob-
client — silently fell out of date. Auto-approve broadcasts allowances to
the v1 addresses, the CLOB tracks the v2 addresses, and the wallet stays
in `no_approvals` forever.

This is the regression test that locks the v2 addresses into the pinned
set. Encoder defense-in-depth (`_check_pinned_spender_or_raise` refusing
unpinned addresses), calldata correctness, and broadcaster idempotency are
already covered by `test_polymarket_approvals_auto_submit.py` (#597) and
`test_polymarket_state_diagnostic.py` (#592) — not duplicated here.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import polymarket_live  # noqa: E402


# Polymarket Exchange v2 addresses — verified on-chain on Polygon mainnet
# as the spenders the CLOB tracks for `AssetType.COLLATERAL` allowance.
# Both are real contracts (eth_getCode returns ~42 KB of bytecode each).
POLYMARKET_CTF_EXCHANGE_V2 = "0xE111180000d2663C0091e4f400237545B87B996B"
POLYMARKET_NEG_RISK_CTF_EXCHANGE_V2 = "0xe2222d279d744050d28e00520010520000310F59"


def test_ctf_exchange_v2_addresses_are_in_pinned_spender_set() -> None:
    """Without these in the pinned set, auto-approve broadcasts to the
    wrong addresses and the CLOB never sees collateral allowance."""

    pinned = {addr.lower() for addr in polymarket_live._PINNED_POLYMARKET_SPENDERS}

    assert POLYMARKET_CTF_EXCHANGE_V2.lower() in pinned, (
        "Polymarket CTF Exchange v2 address missing from pinned spender set; "
        "auto-approve cannot grant the collateral allowance the CLOB tracks"
    )
    assert POLYMARKET_NEG_RISK_CTF_EXCHANGE_V2.lower() in pinned, (
        "Polymarket NegRisk CTF Exchange v2 address missing from pinned "
        "spender set; auto-approve cannot grant the collateral allowance "
        "the CLOB tracks for neg-risk markets"
    )

    # And the calldata-builder defense-in-depth must accept them so the
    # broadcaster can actually encode approve() for these addresses.
    # `_check_pinned_spender_or_raise` returns the lowercased 40-char hex
    # on success and raises ValueError otherwise — a single positive call
    # per address is the smallest assertion that proves both halves.
    assert (
        polymarket_live._check_pinned_spender_or_raise(POLYMARKET_CTF_EXCHANGE_V2)
        == POLYMARKET_CTF_EXCHANGE_V2.lower().removeprefix("0x")
    )
    assert (
        polymarket_live._check_pinned_spender_or_raise(POLYMARKET_NEG_RISK_CTF_EXCHANGE_V2)
        == POLYMARKET_NEG_RISK_CTF_EXCHANGE_V2.lower().removeprefix("0x")
    )
