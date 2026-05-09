"""Critical-only tests for polymarket.prices.

Coverage:
  - test_returns_none_on_publisher_failure: matches the contract — a
    publisher 5xx must not crash the cycle.
  - test_handles_outcome_prices_string_array: Polymarket has shipped at
    least three outcome-price shapes; this is the most surprising one
    (a JSON-encoded string instead of a list) and was the cause of a
    silent zero-price defect in earlier polymarket-bot vintages.
  - test_falls_back_to_best_bid_when_outcomes_missing: newer Gamma
    vintages omit `outcomePrices` and only expose `bestBid`/`bestAsk`.
"""

from __future__ import annotations

from polymarket.prices import fetch_market_price


class _FailingGateway:
    def call(self, *a, **kw):  # noqa: D401, ANN001
        raise RuntimeError("publisher 502")


def test_returns_none_on_publisher_failure() -> None:
    assert fetch_market_price(gateway=_FailingGateway(), condition_id="abc") is None


def test_handles_outcome_prices_string_array(stub_gateway) -> None:
    stub_gateway.register(
        "polymarket-data",
        "GET",
        "/markets/abc",
        {
            "conditionId": "abc",
            "outcomePrices": '["0.62", "0.38"]',
        },
    )
    price = fetch_market_price(gateway=stub_gateway, condition_id="abc")
    assert price is not None
    assert abs(price.yes_price - 0.62) < 1e-9
    assert abs(price.no_price - 0.38) < 1e-9


def test_falls_back_to_best_bid_when_outcomes_missing(stub_gateway) -> None:
    stub_gateway.register(
        "polymarket-data",
        "GET",
        "/markets/abc",
        {
            "conditionId": "abc",
            "bestBid": 0.55,
            "bestAsk": 0.59,
        },
    )
    price = fetch_market_price(gateway=stub_gateway, condition_id="abc")
    assert price is not None
    assert abs(price.yes_price - 0.57) < 1e-9
    assert abs(price.no_price - 0.43) < 1e-9
