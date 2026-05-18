"""DirectClobTrader.create_order must forward CreateOrderOptions to py-clob-client.

Albania E2E (2026-05-18) returned `PolyApiException[status_code=400,
error_message={'error': 'order_version_mismatch'}]` on every hedge submission.
Root cause: `DirectClobTrader.create_order` does
`del tick_size, neg_risk, fee_rate_bps` and then calls
`self._client.create_order(order_args)` without `CreateOrderOptions`. The sister
implementation `PolymarketPublisherTrader.create_order` (same file) passes
`CreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)` explicitly — that's
the proven-working pattern in the other Polymarket skills' publisher path.

py-clob-client 0.34.6 will fall back to a `/neg-risk?token_id=` network probe
when options is None, but the explicit pass-through is the correctness contract:
the caller already fetched `neg_risk` from `/book`, so re-fetching is both
wasted latency and a silent failure surface if the probe disagrees with the
caller's view. The fix is mechanical — match the working pattern.

This test pins the contract at the seam between our trader and py-clob-client
so the bug cannot regress unobserved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _clear_poly_funder(monkeypatch) -> None:
    """POLY_FUNDER from operator shells must not leak into the test."""
    monkeypatch.delenv("POLY_FUNDER", raising=False)


def test_create_order_passes_create_order_options_with_neg_risk_and_tick_size(
    monkeypatch, tmp_path: Path
) -> None:
    """Single critical test: the second arg to py-clob-client's create_order
    must be a CreateOrderOptions carrying the caller's tick_size + neg_risk.

    Asserts both flavors (neg_risk=True, neg_risk=False) so the contract is
    pinned independently of the auto-detect fallback inside py-clob-client.
    """
    # Credentials are read at DirectClobTrader.__init__ — set them to
    # arbitrary strings so the constructor passes its credential preflight.
    private_key = "0x" + "11" * 32  # valid 32-byte hex for eth-account
    monkeypatch.setenv("POLY_PRIVATE_KEY", private_key)
    monkeypatch.setenv("POLY_API_KEY", "test-api-key")
    monkeypatch.setenv("POLY_PASSPHRASE", "test-passphrase")
    monkeypatch.setenv("POLY_SECRET", "test-secret")

    # Stub V2 funder resolver to the EOA-fallback branch so __init__ doesn't
    # try to hit seren-polygon. The funder branch is exercised in
    # test_polymarket_clob_v2_funder.py.
    import polymarket_live

    monkeypatch.setattr(
        polymarket_live,
        "_resolve_v2_funder",
        lambda *, eoa_address: (None, None),
    )

    # Spy on py-clob-client. We capture the exact (args, kwargs) handed to
    # ClobClient.create_order so the test fails loudly if CreateOrderOptions
    # is dropped, mis-keyed, or carries the wrong values.
    captured: dict[str, Any] = {}

    class _FakeSignedOrder:
        pass

    class _FakeClobClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._kwargs = kwargs

        def get_address(self) -> str:
            return "0x" + "aa" * 20

        def create_order(self, order_args: Any, options: Any = None) -> Any:
            captured["order_args"] = order_args
            captured["options"] = options
            return _FakeSignedOrder()

        def post_order(self, signed_order: Any, order_type: Any) -> dict[str, Any]:
            captured["post_order_type"] = order_type
            return {"orderID": "fake-order-id"}

    monkeypatch.setattr(
        "py_clob_client.client.ClobClient",
        _FakeClobClient,
    )

    trader = polymarket_live.DirectClobTrader(
        skill_root=tmp_path,
        client_name="test-direct-clob",
    )

    # --- neg_risk=True: standard mainnet neg-risk market.
    trader.create_order(
        token_id="123456789",
        side="BUY",
        price=0.51,
        size=10.0,
        tick_size="0.01",
        neg_risk=True,
        fee_rate_bps=0,
    )
    options = captured["options"]
    assert options is not None, (
        "DirectClobTrader.create_order called py-clob-client without "
        "CreateOrderOptions; neg_risk markets will be signed against the "
        "standard exchange and rejected with order_version_mismatch."
    )
    assert options.tick_size == "0.01", options
    assert options.neg_risk is True, options

    # --- neg_risk=False: standard exchange market. Same contract.
    captured.clear()
    trader.create_order(
        token_id="987654321",
        side="SELL",
        price=0.49,
        size=10.0,
        tick_size="0.001",
        neg_risk=False,
        fee_rate_bps=0,
    )
    options = captured["options"]
    assert options is not None
    assert options.tick_size == "0.001", options
    assert options.neg_risk is False, options
