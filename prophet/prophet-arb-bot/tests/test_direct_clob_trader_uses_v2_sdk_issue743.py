"""DirectClobTrader must construct a py_clob_client_v2.ClobClient (#743).

PRs #739 and #741 patched py-clob-client 0.34.6 with a custom
`V2OrderBuilder` subclass that rotated the EIP-712 verifyingContract
and domain version. Both shipped with tests and were directionally
correct, but live E2E still returned `order_version_mismatch` because
Polymarket's v2 Order struct itself dropped four fields
(`taker`/`expiration`/`nonce`/`feeRateBps`) and added three
(`timestamp`/`metadata`/`builder`). The fix is not a deeper patch on
the v1 SDK — it is to swap to Polymarket's official `py_clob_client_v2`
SDK, which ships the v2 OrderBuilder and resolves the verifyingContract
per neg-risk flag internally.

This test pins the swap at the one seam that can hide the regression:
`DirectClobTrader._client` MUST be a `py_clob_client_v2.ClobClient`
instance. Everything below it is enforced by the upstream SDK and does
not need re-asserting here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _clear_poly_funder(monkeypatch) -> None:
    monkeypatch.delenv("POLY_FUNDER", raising=False)


def test_direct_clob_trader_constructs_v2_clob_client(monkeypatch, tmp_path: Path) -> None:
    import polymarket_live
    import py_clob_client_v2

    private_key = "0x" + "11" * 32
    monkeypatch.setenv("POLY_PRIVATE_KEY", private_key)
    monkeypatch.setenv("POLY_API_KEY", "test-api-key")
    monkeypatch.setenv("POLY_PASSPHRASE", "test-passphrase")
    monkeypatch.setenv("POLY_SECRET", "test-secret")
    monkeypatch.setattr(
        polymarket_live,
        "_resolve_v2_funder",
        lambda *, eoa_address: (None, None),
    )

    trader = polymarket_live.DirectClobTrader(
        skill_root=tmp_path,
        client_name="test-direct-clob",
    )

    assert isinstance(trader._client, py_clob_client_v2.ClobClient), (
        "DirectClobTrader._client is not a py_clob_client_v2.ClobClient. "
        "The bot will still sign v1 Order structs and the live CLOB will "
        "reject every hedge with order_version_mismatch (#743)."
    )
