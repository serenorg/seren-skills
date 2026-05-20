"""Critical-only test for V2 proxy funder resolution in polymarket_live (#624).

After V2 onboarding, operator funds live as pUSD on the Safe proxy. py-clob-client
defaults `funder=None`, which makes the CLOB read collateral at the EOA address
and return 0 for V2 wallets — `seed_preflight_polymarket_avail=0.0` even with
100+ USDC on the proxy. The fix is `_resolve_v2_funder(eoa)`, which routes the
funder to the V2 proxy when one is deployed and falls back to the EOA otherwise
(preserving V1 wallets).

All four branches of the resolver are pinned in one test because they share the
same envelope contract `(funder, signature_type)` and any regression in any
branch reintroduces the same operator-visible symptom (CLOB collateral=0). The
ClobClient(...) plumbing itself is a one-line pass-through; covering it
separately would duplicate the resolver test without adding signal.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_poly_funder(monkeypatch) -> None:
    """POLY_FUNDER / POLY_DEPOSIT_WALLET leaking from the operator's shell
    would mask the auto-resolution branches under test."""
    monkeypatch.delenv("POLY_FUNDER", raising=False)
    monkeypatch.delenv("POLY_DEPOSIT_WALLET", raising=False)
    monkeypatch.delenv("POLY_SIGNATURE_TYPE", raising=False)


EOA = "0x000000000000000000000000000000000000000A"
PROXY = "0x312eEa8c2598e5d1CD932F480bEC6dE1DA093A24"


def test_resolve_v2_funder_handles_all_four_branches(monkeypatch) -> None:
    from polymarket_live import _resolve_v2_funder

    # --- Branch 1: V2 proxy resolved AND deployed -> route to proxy.
    # signature_type=2 is py-clob-client's POLY_GNOSIS_SAFE — required so
    # the CLOB verifies the order against the Safe owner key, not the EOA.
    monkeypatch.setattr(
        "polymarket_v2_broadcast.fetch_proxy_address_for_eoa",
        lambda *, eoa_address: PROXY,
    )
    monkeypatch.setattr(
        "polymarket_v2_broadcast.fetch_eth_get_code",
        # Non-"0x" bytecode means the proxy is deployed.
        lambda address: "0x60806040",
    )
    funder, sig_type = _resolve_v2_funder(eoa_address=EOA)
    assert funder == PROXY
    assert sig_type == 2

    # --- Branch 2: proxy resolves but NOT deployed (fresh V2 wallet
    # pre-onboarding) -> EOA fallback. The CLOB has no way to recognize
    # an undeployed Safe as a funder, so this must NOT switch.
    monkeypatch.setattr(
        "polymarket_v2_broadcast.fetch_eth_get_code",
        lambda address: "0x",
    )
    funder, sig_type = _resolve_v2_funder(eoa_address=EOA)
    assert funder is None
    assert sig_type is None

    # --- Branch 3: RPC failure resolving the proxy (publisher down) ->
    # EOA fallback. Conservative: when in doubt, do NOT misroute the
    # funder. Keeps V1 wallets working when seren-polygon is unavailable.
    monkeypatch.setattr(
        "polymarket_v2_broadcast.fetch_proxy_address_for_eoa",
        lambda *, eoa_address: None,
    )
    funder, sig_type = _resolve_v2_funder(eoa_address=EOA)
    assert funder is None
    assert sig_type is None

    # --- Branch 4: POLY_FUNDER explicit operator override -> use it
    # verbatim with signature_type=2. This must short-circuit the
    # RPC probes entirely — operators set this when running offline
    # or against a custom funder address.
    monkeypatch.setenv("POLY_FUNDER", PROXY)
    # Even with proxy-resolver returning None, the env override wins:
    funder, sig_type = _resolve_v2_funder(eoa_address=EOA)
    assert funder == PROXY
    assert sig_type == 2


DEPOSIT_WALLET = "0x000000000000000000000000000000000000DEAD"


def test_resolve_v2_funder_routes_to_deposit_wallet_v2(monkeypatch) -> None:
    """#745: POLY_DEPOSIT_WALLET must route to (deposit_wallet, 3=POLY_1271).

    Polymarket migrated CLOB to v2 on 2026-04-28. The v2 validator rejects
    every signature_type=2 (POLY_GNOSIS_SAFE) order with `maker address not
    allowed, please use the deposit wallet flow`. Only signature_type=3
    (POLY_1271 / ERC-7739) against a CREATE2-derived deposit wallet is
    accepted. Until CREATE2 derivation lands in-tree, operators set
    POLY_DEPOSIT_WALLET to the address Polymarket's UI provisioned for
    them, and the resolver routes both sides of the (funder, sig_type)
    envelope in one shot.

    Precedence matters: a stale POLY_FUNDER from a pre-#745 install must
    NOT override the deposit-wallet path. POLY_FUNDER signs with sig_type=2,
    which the v2 CLOB rejects — silently honoring it would leave the user
    back at `maker address not allowed`.
    """
    from polymarket_live import _resolve_v2_funder

    monkeypatch.setenv("POLY_DEPOSIT_WALLET", DEPOSIT_WALLET)
    # Stale POLY_FUNDER from a v1 install must NOT win:
    monkeypatch.setenv("POLY_FUNDER", PROXY)

    funder, sig_type = _resolve_v2_funder(eoa_address=EOA)
    assert funder == DEPOSIT_WALLET
    assert sig_type == 3
