"""Arb-bot funds preflight (#524).

Issue #524: `placeOrder` locks USDC as collateral on Prophet's CTF
order book. Without a preflight, every cron tick on an unfunded wallet
attempts N `placeOrder` mutations that all reject with insufficient
funds. Cheap per-call but it floods the events table with rows that
all share the same root cause.

Critical-path tests:
  1. `MinimalProphetClient.cash_balance` decodes the
     `ViewerWalletBalance` payload (operation name aligned with sibling
     skills).
  2. `evaluate_funds_preflight` returns ok when balance covers the
     planned orders.
  3. `evaluate_funds_preflight` returns a structured deficit when
     balance is short — same envelope shape as the bounty-runner.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from prophet.client import MinimalProphetClient, ViewerCashBalance
from funds_preflight import evaluate_funds_preflight


@dataclass
class _StubOpp:
    """Smallest object that satisfies `evaluate_funds_preflight`.

    The real `Opportunity` carries far more fields; the preflight only
    reads `size_usdc`. Keeping the stub local keeps this test from
    coupling to the scoring module.
    """

    size_usdc: float
    prophet_market_id: str = "m1"
    outcome: str = "YES"
    side: str = "BUY"


def test_cash_balance_decodes_viewer_wallet_balance_payload(stub_transport) -> None:
    stub_transport.register(
        "ViewerWalletBalance",
        {"data": {"viewer": {"walletBalance": {"availableCents": 4242, "totalCents": 4242}}}},
    )
    client = MinimalProphetClient(transport=stub_transport)

    balance = client.cash_balance(jwt="eyJ.fake.jwt")

    assert isinstance(balance, ViewerCashBalance)
    assert balance.available_cents == 4242
    assert balance.available_usdc == 42.42
    # Operation name must match what sibling skills use; otherwise the
    # captured schema fixtures drift apart.
    call = next(c for c in stub_transport.calls if c["operation_name"] == "ViewerWalletBalance")
    assert "ViewerWalletBalance" in call["query"]


def test_preflight_ok_when_balance_covers_all_planned_orders() -> None:
    opps = [_StubOpp(size_usdc=5.0), _StubOpp(size_usdc=3.0)]

    result = evaluate_funds_preflight(opportunities=opps, available_usdc=10.0)

    assert result.ok is True
    assert result.deficit_usdc == pytest.approx(0.0)
    assert result.needed_usdc == pytest.approx(8.0)


def test_preflight_blocks_with_structured_deficit_when_short() -> None:
    opps = [_StubOpp(size_usdc=5.0), _StubOpp(size_usdc=3.0)]

    result = evaluate_funds_preflight(opportunities=opps, available_usdc=2.0)

    assert result.ok is False
    assert result.needed_usdc == pytest.approx(8.0)
    assert result.available_usdc == pytest.approx(2.0)
    assert result.deficit_usdc == pytest.approx(6.0)


def test_preflight_ok_when_no_opportunities() -> None:
    """Empty opportunity list is a no-op — don't block, don't query."""
    result = evaluate_funds_preflight(opportunities=[], available_usdc=0.0)

    assert result.ok is True
    assert result.needed_usdc == 0.0
    assert result.deficit_usdc == 0.0
