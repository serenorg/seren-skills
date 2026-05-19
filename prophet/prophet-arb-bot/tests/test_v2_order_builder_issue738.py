"""V2 OrderBuilder must sign Polymarket CLOB orders with v2 exchange addresses (#738).

Live Albania run (2026-05-18) confirmed every hedge submission returned
`PolyApiException[status_code=400, error_message={'error': 'order_version_mismatch'}]`
even after CreateOrderOptions forwarding shipped in #737. Root cause: py-clob-client
0.34.6's bundled `OrderBuilder` calls `get_contract_config(137, neg_risk)` and signs
EIP-712 orders with the v1 exchange addresses (standard `0x4bFb…`, neg-risk
`0xC5d563…`). Polymarket's live CLOB validator now rejects v1-signed orders and
expects v2: standard `0xE11118…`, neg-risk `0xe2222d…`. These are the same v2
spenders the prophet-arb-bot SKILL.md pins for auto-approve (#600).

Two critical tests pin the contract at the only seam that can hide the regression:

1. `V2OrderBuilder.create_order` must construct the underlying py_order_utils
   `OrderBuilder` with the v2 standard exchange when `neg_risk=False` and the
   v2 neg-risk exchange when `neg_risk=True`. The `exchange_address` passed to
   the utils builder IS the EIP-712 `verifyingContract` baked into the signed
   order's domain separator — getting it wrong is exactly the v1/v2 drift
   that produces `order_version_mismatch`.

2. `DirectClobTrader.__init__` must replace `self._client.builder` with a
   `V2OrderBuilder` instance after constructing the `ClobClient`. Without the
   swap, the v2 subclass is dead code and v1 orders still ship.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _clear_poly_funder(monkeypatch) -> None:
    """POLY_FUNDER from operator shells must not leak into the test."""
    monkeypatch.delenv("POLY_FUNDER", raising=False)


def test_v2_order_builder_signs_with_v2_exchange_address(monkeypatch) -> None:
    """Both neg_risk flavors must route to the correct v2 exchange address.

    The `exchange_address` first positional arg to the underlying
    py_order_utils `OrderBuilder` is baked into the EIP-712 `verifyingContract`
    via `BaseBuilder._get_domain_separator`. Asserting on that arg is
    equivalent to asserting on the signed order's verifyingContract — and
    avoids the heavier path of reconstructing the EIP-712 hash and
    ec-recovering the signature.
    """
    import polymarket_v2_order_builder as mod
    from py_clob_client.clob_types import CreateOrderOptions, OrderArgs
    from py_clob_client.signer import Signer as ClobSigner

    captured: list[str] = []

    class _FakeUtilsBuilder:
        def __init__(self, exchange_address: str, chain_id: int, utils_signer: Any) -> None:
            captured.append(exchange_address.lower())

        def build_signed_order(self, _data: Any) -> Any:
            return object()

    monkeypatch.setattr(mod, "UtilsOrderBuilder", _FakeUtilsBuilder)

    private_key = "0x" + "11" * 32
    signer = ClobSigner(private_key=private_key, chain_id=137)
    builder = mod.V2OrderBuilder(signer)

    builder.create_order(
        OrderArgs(token_id="123", price=0.51, size=10.0, side="BUY"),
        CreateOrderOptions(tick_size="0.01", neg_risk=False),
    )
    builder.create_order(
        OrderArgs(token_id="456", price=0.51, size=10.0, side="BUY"),
        CreateOrderOptions(tick_size="0.01", neg_risk=True),
    )

    assert captured == [
        mod.POLYMARKET_V2_EXCHANGE_STANDARD.lower(),
        mod.POLYMARKET_V2_EXCHANGE_NEG_RISK.lower(),
    ], (
        "V2OrderBuilder must select the v2 exchange address based on "
        f"options.neg_risk; got {captured}"
    )


def test_direct_clob_trader_installs_v2_order_builder(monkeypatch, tmp_path: Path) -> None:
    """DirectClobTrader.__init__ must swap ClobClient.builder for V2OrderBuilder.

    Without this swap the V2OrderBuilder subclass is unreachable and the
    bundled py-clob-client v1 builder still signs every order — the exact
    failure mode the live Albania run hit on 2026-05-18.
    """
    import polymarket_live
    import polymarket_v2_order_builder as v2mod
    from py_clob_client.signer import Signer as ClobSigner

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

    class _FakeClobClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            # Real ClobClient builds .signer + .builder in __init__; mimic
            # that so DirectClobTrader's swap path has something to replace.
            self.signer = ClobSigner(private_key=private_key, chain_id=137)
            self.builder = "v1-original-must-be-replaced"

        def get_address(self) -> str:
            return self.signer.address()

    monkeypatch.setattr("py_clob_client.client.ClobClient", _FakeClobClient)

    trader = polymarket_live.DirectClobTrader(
        skill_root=tmp_path,
        client_name="test-direct-clob",
    )

    assert isinstance(trader._client.builder, v2mod.V2OrderBuilder), (
        "DirectClobTrader did not install V2OrderBuilder. Orders will be "
        "signed against the py-clob-client v1 exchange and rejected with "
        "order_version_mismatch (#738)."
    )
