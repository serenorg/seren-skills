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


def test_place_order_validates_inputs(stub_transport) -> None:
    client = ProphetOrderClient(transport=stub_transport)
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
    # Validation must run before any transport call.
    assert stub_transport.calls == []


def test_place_order_fails_closed_when_no_id(stub_transport) -> None:
    stub_transport.register_default(
        {"data": {"placeOrder": {"order": None}}},
    )
    client = ProphetOrderClient(transport=stub_transport)
    with pytest.raises(ProphetSchemaError, match="placeOrder did not return an order.id"):
        client.place_order(
            jwt="x",
            market_id="m1",
            outcome="yes",
            side="buy",
            shares=1.0,
            limit_price=0.5,
        )


def test_place_order_unwraps_graphql_errors(stub_transport) -> None:
    # Issue #493: top-level GraphQL `errors[]` is now surfaced by
    # ProphetDirectTransport.post_graphql as ProphetGraphQLError; the
    # client itself never sees the raw payload. Register the exception
    # so the stub mimics the production transport's behavior.
    stub_transport.register_default(
        ProphetGraphQLError("PlaceOrderInput.type required"),
    )
    client = ProphetOrderClient(transport=stub_transport)
    with pytest.raises(
        ProphetGraphQLError, match="PlaceOrderInput.type required"
    ):
        client.place_order(
            jwt="x",
            market_id="m1",
            outcome="yes",
            side="buy",
            shares=1.0,
            limit_price=0.5,
        )


def test_place_order_sends_live_schema_input_shape(stub_transport) -> None:
    """Pin the GraphQL input shape to the live Prophet schema (issue #477).

    PlaceOrderInput requires: marketId, outcome (YES|NO), type (LIMIT|MARKET),
    side (BUY|SELL), priceBps (Int 0-10000), quantity (Float, in shares).
    timeInForce (GTC default) is optional. The previous best-guess sent
    `limitPrice`/`shares` which Prophet rejects, and omitted the required
    `type` field entirely.
    """
    stub_transport.register_default(
        {
            "data": {
                "placeOrder": {
                    "order": {
                        "id": "ord_1",
                        "market": {"id": "m1"},
                        "outcome": "YES",
                        "side": "BUY",
                        "type": "LIMIT",
                        "priceBps": 5000,
                        "quantityShares": 20.0,
                        "filledShares": 0.0,
                        "status": "OPEN",
                    }
                }
            }
        },
    )
    client = ProphetOrderClient(transport=stub_transport)
    order = client.place_order(
        jwt="x",
        market_id="m1",
        outcome="yes",
        side="buy",
        shares=10.0,  # USDC notional
        limit_price=0.5,
    )

    call = stub_transport.calls[0]
    body = {"query": call["query"], "variables": call["variables"]}
    sent_input = body["variables"]["input"]
    # Required fields exactly as Prophet's PlaceOrderInput expects.
    assert sent_input["marketId"] == "m1"
    assert sent_input["outcome"] == "YES"
    assert sent_input["side"] == "BUY"
    assert sent_input["type"] == "LIMIT"
    assert sent_input["priceBps"] == 5000  # 0.5 * 10000
    # quantity = USDC notional / limit_price = shares
    assert sent_input["quantity"] == 20.0
    assert sent_input.get("timeInForce", "GTC") == "GTC"
    # Old field names must not leak.
    assert "limitPrice" not in sent_input
    assert "shares" not in sent_input
    # Selection set must use Order's real field names.
    assert "quantityShares" in body["query"]
    assert "priceBps" in body["query"]
    assert "market { id }" in body["query"] or "market{id}" in body["query"].replace(" ", "")
    # Returned ProphetOrder maps live-schema fields back to the dataclass.
    assert order.market_id == "m1"
    assert order.shares == 20.0
    assert order.limit_price == 0.5  # priceBps 5000 -> 0.5


def test_cancel_order_sends_input_object_and_inspects_errors(stub_transport) -> None:
    """Pin cancelOrder shape (issue #477).

    Live schema is `cancelOrder(input: CancelOrderInput!)` returning
    `CancelOrderPayload { order, errors }`. The previous guess used
    `cancelOrder(orderId: ID!)` and asked for a non-existent `ok` field.
    """
    stub_transport.register_default(
        {
            "data": {
                "cancelOrder": {
                    "order": {"id": "ord_1", "status": "CANCELLED"},
                    "errors": None,
                }
            }
        },
    )
    client = ProphetOrderClient(transport=stub_transport)
    ok = client.cancel_order(jwt="x", order_id="ord_1")

    call = stub_transport.calls[0]
    body = {"query": call["query"], "variables": call["variables"]}
    assert body["variables"] == {"input": {"orderId": "ord_1"}}
    # Selection set must not request the non-existent `ok` field.
    assert " ok" not in body["query"]
    assert "errors" in body["query"]
    assert ok is True


def test_cancel_order_returns_false_when_payload_has_errors(stub_transport) -> None:
    """A non-empty errors[] on CancelOrderPayload means cancel did not happen."""
    stub_transport.register_default(
        {
            "data": {
                "cancelOrder": {
                    "order": None,
                    "errors": [{"message": "order already filled"}],
                }
            }
        },
    )
    client = ProphetOrderClient(transport=stub_transport)
    assert client.cancel_order(jwt="x", order_id="ord_1") is False


def test_list_user_orders_uses_viewer_orders_relay_shape(stub_transport) -> None:
    """Pin the live `viewer.orders` Relay shape (issue #478).

    The previous query hit `Query.userOrders` which does not exist in
    Prophet's schema, so the dedupe path silently failed every tick.
    The real path is `viewer.orders { edges { node { ... } } }` returning
    Order records with `market { id }`, `priceBps`, `quantityShares`.
    """
    stub_transport.register_default(
        {
            "data": {
                "viewer": {
                    "orders": {
                        "edges": [
                            {
                                "node": {
                                    "id": "ord_open_1",
                                    "market": {"id": "mkt_a"},
                                    "outcome": "YES",
                                    "side": "BUY",
                                    "type": "LIMIT",
                                    "priceBps": 4200,
                                    "quantityShares": 23.81,
                                    "filledShares": 0.0,
                                    "remainingShares": 23.81,
                                    "status": "OPEN",
                                }
                            },
                            {
                                "node": {
                                    "id": "ord_open_2",
                                    "market": {"id": "mkt_b"},
                                    "outcome": "NO",
                                    "side": "SELL",
                                    "type": "LIMIT",
                                    "priceBps": 5500,
                                    "quantityShares": 9.0,
                                    "filledShares": 1.0,
                                    "remainingShares": 8.0,
                                    "status": "PARTIALLY_FILLED",
                                }
                            },
                        ]
                    }
                }
            }
        },
    )
    client = ProphetOrderClient(transport=stub_transport)
    orders = client.list_user_orders(jwt="x", status="OPEN")

    call = stub_transport.calls[0]
    body = {"query": call["query"], "variables": call["variables"]}
    # Walk viewer.orders.edges[].node, not the dead userOrders path.
    assert "viewer" in body["query"]
    assert "edges" in body["query"]
    assert "node" in body["query"]
    assert "userOrders" not in body["query"]
    # Selection set must use Order's real field names.
    assert "quantityShares" in body["query"]
    assert "priceBps" in body["query"]
    assert "market { id }" in body["query"] or "market{id}" in body["query"].replace(
        " ", ""
    )

    assert len(orders) == 2
    assert orders[0].order_id == "ord_open_1"
    assert orders[0].market_id == "mkt_a"
    assert orders[0].outcome == "yes"
    assert orders[0].side == "buy"
    assert orders[0].limit_price == 0.42  # priceBps 4200 / 10000
    assert orders[0].shares == 23.81
    assert orders[0].status == "open"

    assert orders[1].market_id == "mkt_b"
    assert orders[1].limit_price == 0.55
    assert orders[1].filled_shares == 1.0


def test_list_user_orders_market_filter_is_client_side(stub_transport) -> None:
    """`market_id` is a client-side filter today — OrdersInput shape unknown.

    The agent dedupes by (market_id, outcome, side); the client must drop
    nodes whose market.id does not match the requested filter rather than
    relying on a server-side argument that we have not yet introspected.
    """
    stub_transport.register_default(
        {
            "data": {
                "viewer": {
                    "orders": {
                        "edges": [
                            {
                                "node": {
                                    "id": "ord_a",
                                    "market": {"id": "mkt_a"},
                                    "outcome": "YES",
                                    "side": "BUY",
                                    "priceBps": 5000,
                                    "quantityShares": 10.0,
                                    "filledShares": 0.0,
                                    "status": "OPEN",
                                }
                            },
                            {
                                "node": {
                                    "id": "ord_b",
                                    "market": {"id": "mkt_b"},
                                    "outcome": "YES",
                                    "side": "BUY",
                                    "priceBps": 5000,
                                    "quantityShares": 10.0,
                                    "filledShares": 0.0,
                                    "status": "OPEN",
                                }
                            },
                        ]
                    }
                }
            }
        },
    )
    client = ProphetOrderClient(transport=stub_transport)
    orders = client.list_user_orders(jwt="x", market_id="mkt_b", status="OPEN")
    assert [o.order_id for o in orders] == ["ord_b"]


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
