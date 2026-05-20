"""Critical-only tests for POLY_PROXY (sig_type=1) account support.

The resolver in #624/#745 only emits sig_type=2 (POLY_GNOSIS_SAFE) or
sig_type=3 (POLY_1271). Existing POLY_PROXY (sig_type=1) Polymarket
accounts hold non-zero collateral and have full v2 spender allowances,
but every `/order` for those accounts is rejected with
`maker address not allowed, please use the deposit wallet flow` because
the bot signs with the wrong sig_type for the account's contract type.

Two escape hatches close the gap:

  1. `POLY_SIGNATURE_TYPE` env override — operator who knows their
     account type (POLY_PROXY=1, POLY_GNOSIS_SAFE=2, POLY_1271=3) sets
     it alongside `POLY_FUNDER` and the resolver returns the override
     verbatim. POLY_DEPOSIT_WALLET path is semantically tied to
     POLY_1271 and ignores the override (sig_type=3 is the only valid
     pairing).

  2. `_autodetect_signature_type` — probes `/balance-allowance` under
     candidate sig_types and returns the first one with non-zero
     balance. Used by `DirectClobTrader.__init__` when no override is
     set, so legacy POLY_PROXY operators get the right sig_type
     without any env-var spelunking.

Both paths are pinned together because they share the same envelope
contract `(funder, signature_type)` as the existing resolver tests
and a regression in either reintroduces the operator-visible
`maker address not allowed` symptom.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_poly_env(monkeypatch) -> None:
    """POLY_* leaking from the operator's shell would mask the
    auto-resolution and override branches under test."""
    monkeypatch.delenv("POLY_FUNDER", raising=False)
    monkeypatch.delenv("POLY_DEPOSIT_WALLET", raising=False)
    monkeypatch.delenv("POLY_SIGNATURE_TYPE", raising=False)


EOA = "0x000000000000000000000000000000000000000A"
PROXY = "0x000000000000000000000000000000000000B0B0"
DEPOSIT_WALLET = "0x000000000000000000000000000000000000DEAD"


def test_poly_signature_type_override(monkeypatch) -> None:
    """POLY_SIGNATURE_TYPE pairs with POLY_FUNDER, ignored by POLY_DEPOSIT_WALLET.

    Pins four sub-branches in one test because they share the same
    `(funder, sig_type)` envelope and any regression reintroduces the
    `maker address not allowed` error for POLY_PROXY operators.
    """
    from polymarket_live import _resolve_v2_funder

    # Branch 1: POLY_SIGNATURE_TYPE=1 + POLY_FUNDER -> (funder, 1).
    # This is the POLY_PROXY escape: existing legacy proxy accounts
    # whose orders the v2 CLOB will accept under sig_type=1.
    monkeypatch.setenv("POLY_FUNDER", PROXY)
    monkeypatch.setenv("POLY_SIGNATURE_TYPE", "1")
    assert _resolve_v2_funder(eoa_address=EOA) == (PROXY, 1)

    # Branch 2: POLY_SIGNATURE_TYPE=3 + POLY_FUNDER -> (funder, 3).
    # Operator with a POLY_1271 deposit wallet who already has POLY_FUNDER
    # set in their shell can force sig_type=3 without renaming env vars.
    monkeypatch.setenv("POLY_SIGNATURE_TYPE", "3")
    assert _resolve_v2_funder(eoa_address=EOA) == (PROXY, 3)

    # Branch 3: invalid POLY_SIGNATURE_TYPE -> falls back to default
    # sig_type=2 on the POLY_FUNDER path. Conservative: a typo must
    # not silently switch the signature scheme.
    monkeypatch.setenv("POLY_SIGNATURE_TYPE", "not-an-int")
    assert _resolve_v2_funder(eoa_address=EOA) == (PROXY, 2)

    # Branch 4: POLY_DEPOSIT_WALLET ignores POLY_SIGNATURE_TYPE.
    # POLY_DEPOSIT_WALLET is semantically tied to POLY_1271 / sig_type=3
    # — honoring an override here would let an operator submit orders
    # with a mismatched signature against the deposit-wallet contract,
    # which the v2 CLOB rejects.
    monkeypatch.setenv("POLY_DEPOSIT_WALLET", DEPOSIT_WALLET)
    monkeypatch.setenv("POLY_SIGNATURE_TYPE", "1")
    assert _resolve_v2_funder(eoa_address=EOA) == (DEPOSIT_WALLET, 3)


def test_autodetect_signature_type_picks_first_nonzero_and_falls_back(monkeypatch) -> None:
    """Autodetect probes /balance-allowance under candidate sig_types
    and returns the first one with non-zero balance, or None.

    Pins both the happy path (POLY_PROXY account hits non-zero at
    sig_type=1) and the all-zero fallback (no candidate has balance,
    so caller must use the resolver default). Combining them is
    intentional: they share the same probe loop and any regression
    in iteration order or zero handling breaks both.
    """
    from polymarket_live import _autodetect_signature_type

    # Branch 1: POLY_PROXY account — sig_type=1 returns non-zero,
    # sig_type=2 and 3 return zero. Autodetect must pick 1, not
    # silently downgrade to None just because some candidates miss.
    balances_proxy_account = {1: 109_000_000, 2: 0, 3: 0}
    calls: list[int] = []

    def probe_proxy(*, funder, signature_type):
        assert funder == PROXY
        calls.append(signature_type)
        return balances_proxy_account[signature_type]

    assert _autodetect_signature_type(funder=PROXY, probe_balance=probe_proxy) == 1
    # Probe must short-circuit on first non-zero hit (no need to call
    # sig_type=2 or 3 once sig_type=1 is positive) — keeps startup
    # latency at one CLOB round trip when the operator's account
    # is the most common legacy POLY_PROXY shape.
    assert calls == [1]

    # Branch 2: all candidates return zero (e.g. operator on a v2
    # deposit wallet that hasn't been funded yet). Autodetect must
    # return None so the caller falls back to the resolver default
    # rather than blindly forcing a sig_type with no balance behind it.
    def probe_empty(*, funder, signature_type):
        return 0

    assert _autodetect_signature_type(funder=PROXY, probe_balance=probe_empty) is None

    # Branch 3: probe raises on every candidate (CLOB API down).
    # Autodetect must return None, not propagate the exception —
    # startup must degrade to "use resolver default" rather than
    # crash the trader and block the whole cycle.
    def probe_raises(*, funder, signature_type):
        raise RuntimeError("clob unreachable")

    assert _autodetect_signature_type(funder=PROXY, probe_balance=probe_raises) is None
