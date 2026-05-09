"""Critical-only tests for prophet.orders.

Coverage:
  - test_place_order_validates_inputs: catches obviously-bad arguments
    before they reach the publisher (limit_price out of bounds, bad
    outcome string). Prophet would 400 these but we'd waste a publisher
    call and a SerenBucks debit.
  - test_place_order_fails_closed_when_no_id: locks the §3 ADR contract
    that any unrecognized response shape raises ProphetSchemaError. If
    we ever silently swallow this, the operator can't tell a successful
    order from a no-op.
  - test_place_order_unwraps_graphql_errors: Prophet returns 200 with
    `errors` on logical failures; we must surface those as
    ProphetGraphQLError, not as success.
  - test_market_prices_parses_dict_outcome_shape: tolerance test —
    Prophet's outcome shape has changed between vintages and our
    parser must handle both.
"""

from __future__ import annotations

import pytest

from prophet import ProphetGraphQLError, ProphetSchemaError
from prophet.orders import ProphetOrderClient, _parse_outcomes


def test_place_order_validates_inputs(stub_gateway) -> None:
    client = ProphetOrderClient(gateway=stub_gateway)
    with pytest.raises(ValueError, match="limit_price"):
        client.place_order(
            jwt="x",
            market_id="m1",
            outcome="yes",
            side="buy",
            shares=1.0,
            limit_price=1.5,  # > 1
        )
    with pytest.raises(ValueError, match="outcome"):
        client.place_order(
            jwt="x",
            market_id="m1",
            outcome="maybe",
            side="buy",
            shares=1.0,
            limit_price=0.5,
        )
    with pytest.raises(ValueError, match="side"):
        client.place_order(
            jwt="x",
            market_id="m1",
            outcome="yes",
            side="hedge",
            shares=1.0,
            limit_price=0.5,
        )
    with pytest.raises(ValueError, match="shares"):
        client.place_order(
            jwt="x",
            market_id="m1",
            outcome="yes",
            side="buy",
            shares=0,
            limit_price=0.5,
        )
    # Validation must run before any gateway call.
    assert stub_gateway.calls == []


def test_place_order_fails_closed_when_no_id(stub_gateway) -> None:
    stub_gateway.register(
        "prophet-ai",
        "POST",
        "/api/graphql",
        {"data": {"placeOrder": {"order": None}}},
    )
    client = ProphetOrderClient(gateway=stub_gateway)
    with pytest.raises(ProphetSchemaError, match="placeOrder did not return an order.id"):
        client.place_order(
            jwt="x",
            market_id="m1",
            outcome="yes",
            side="buy",
            shares=1.0,
            limit_price=0.5,
        )


def test_place_order_unwraps_graphql_errors(stub_gateway) -> None:
    stub_gateway.register(
        "prophet-ai",
        "POST",
        "/api/graphql",
        {"errors": [{"message": "PlaceOrderInput.limitPrice required"}]},
    )
    client = ProphetOrderClient(gateway=stub_gateway)
    with pytest.raises(
        ProphetGraphQLError, match="PlaceOrderInput.limitPrice required"
    ):
        client.place_order(
            jwt="x",
            market_id="m1",
            outcome="yes",
            side="buy",
            shares=1.0,
            limit_price=0.5,
        )


def test_market_prices_parses_dict_outcome_shape() -> None:
    yes, no = _parse_outcomes({"yes": 0.62, "no": 0.38})
    assert (yes, no) == (0.62, 0.38)
    yes, no = _parse_outcomes(
        [
            {"name": "Yes", "price": 0.55},
            {"name": "No", "price": 0.45},
        ]
    )
    assert (yes, no) == (0.55, 0.45)
    # Unknown shape returns zeros — caller flags as untradable.
    yes, no = _parse_outcomes("nonsense")
    assert (yes, no) == (0.0, 0.0)
