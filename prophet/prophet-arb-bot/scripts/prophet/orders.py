"""Prophet order operations — live-validated GraphQL (#505 Phase 15).

prophet-bounty-runner only ships market creation. The arb-bot ships the
trading half: place limit orders, cancel them, list outstanding orders,
and read live odds.

The mutation shapes were live-validated against Prophet's production
GraphQL endpoint (2026-05-13) and pinned against the captured schema
fixture `tests/fixtures/prophet_schema.json`. They are **not** best
guesses any more:

  - `placeOrder(input: PlaceOrderInput!)` is **server-signed**. Unlike
    `createMarketWithBet`, `PlaceOrderInput` does NOT carry a
    `SignedOrderInput` — Prophet's backend signs the CTF order on
    behalf of the user via Privy session signers. Just the Privy JWT
    is sufficient. Fields: `{marketId, outcome, type, side, priceBps,
    quantity, timeInForce}`.
  - `cancelOrder(input: CancelOrderInput!)` likewise needs only the
    JWT; CancelOrderInput is `{orderId: ID!}`.
  - `userOrders(marketId: ID, status: OrderStatus)` is unchanged.

If the live schema returns an unexpected error the gateway still
raises `ProphetGraphQLError` and `ProphetSchemaError` for callers to
treat as hard fail-closed signals — but a re-introspection is the
right remediation, not a shape rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import ProphetGraphQLError, ProphetSchemaError


@dataclass
class ProphetMarketPrices:
    """Subset of Market fields used for arb scoring.

    Prophet markets are binary; `yes_price` and `no_price` should sum to
    ~1.0. We store both rather than deriving one from the other so a
    schema drift that leaves them inconsistent surfaces in scoring
    rather than silently mispricing.
    """

    market_id: str
    slug: str
    yes_price: float
    no_price: float
    resolution_date: str


@dataclass
class ProphetOrder:
    order_id: str
    market_id: str
    outcome: str  # "yes" | "no"
    side: str  # "buy" | "sell"
    shares: float
    limit_price: float
    filled_shares: float
    status: str  # "open" | "filled" | "cancelled" | "expired"


class ProphetOrderClient:
    """Order operations against Prophet.

    Composition over inheritance: takes the same `transport` seam used by
    `MinimalProphetClient` so tests can swap a stub. The two clients
    share `prophet.transport.ProphetDirectTransport` as their HTTP path.

    Issue #493: the previous `gateway.call("prophet-ai", ...)` hop was
    removed because the Seren publisher proxy reserves the
    `Authorization` header for SEREN_API_KEY billing auth — Prophet's
    `Authorization: Bearer <JWT>` could not ride that slot through the
    proxy. We now talk to `app.prophetmarket.ai` directly.
    """

    def __init__(self, *, transport: Any) -> None:
        self.transport = transport

    # ------------------------------------------------------------------
    # Reads

    def market_prices(self, *, jwt: str | None, market_id: str) -> ProphetMarketPrices:
        """Live odds snapshot for an arb scoring pass.

        Schema guess (Phase 2 / §3 ADR):
          Market.outcomes is an array of `{name, price}` objects where
          `name` is "Yes" / "No" (or platform synonyms) and `price` is a
          0-1 probability. The bounty-runner's `MarketById` query exposed
          `slug`/`resolutionDate`; we extend that with `outcomes` here.

        Until the fixture lands the response shape is treated tolerantly —
        we read whichever of `outcomes`, `prices`, or `odds` the publisher
        actually returns. A schema rejection raises ProphetSchemaError so
        the agent can flag the run as blocked instead of trading on stale
        data.
        """
        query = """
        query MarketPrices($id: ID!) {
          market(id: $id) {
            id
            slug
            resolutionDate
            outcomes { name price }
          }
        }
        """
        payload = self._post(jwt=jwt, query=query, variables={"id": market_id})
        market = ((payload or {}).get("data") or {}).get("market") or {}
        if not market.get("id"):
            raise ProphetSchemaError(f"market({market_id}) returned no record")
        outcomes = market.get("outcomes")
        yes_price, no_price = _parse_outcomes(outcomes)
        return ProphetMarketPrices(
            market_id=market.get("id") or "",
            slug=market.get("slug") or "",
            yes_price=yes_price,
            no_price=no_price,
            resolution_date=market.get("resolutionDate") or "",
        )

    def list_user_orders(
        self,
        *,
        jwt: str,
        market_id: str | None = None,
        status: str | None = None,
    ) -> list[ProphetOrder]:
        """User's outstanding orders via Prophet's Relay-style viewer.orders.

        Live schema:
            type Viewer { orders(input: OrdersInput): OrderConnection! }
            type OrderConnection { edges: [OrderEdge!]! }
            type OrderEdge       { node: Order!, cursor: String! }

        OrdersInput shape is not yet introspected, so market_id and status
        filters are applied client-side after fetching the connection. This
        is the cross-tick dedupe input — silent failures here can lead to
        double-quoting, so any unexpected response shape raises.
        """
        query = """
        query ViewerOrders {
          viewer {
            orders {
              edges {
                node {
                  id
                  market { id }
                  outcome
                  side
                  type
                  priceBps
                  quantityShares
                  filledShares
                  remainingShares
                  status
                }
              }
            }
          }
        }
        """
        payload = self._post(jwt=jwt, query=query, variables={})
        viewer = ((payload or {}).get("data") or {}).get("viewer") or {}
        orders_field = viewer.get("orders")
        if orders_field is None:
            raise ProphetSchemaError(
                "viewer.orders missing — Prophet schema may have drifted"
            )
        edges = orders_field.get("edges") if isinstance(orders_field, dict) else None
        if not isinstance(edges, list):
            raise ProphetSchemaError(
                "viewer.orders.edges did not return a list — schema may have drifted"
            )

        # Dedupe semantics: callers pass status="OPEN" meaning "any order
        # that would conflict with placing a new one". Partially filled
        # and pending orders both still rest on the book, so treat them as
        # active. For any other status, match exactly.
        wanted_status = (status or "").lower() or None
        ACTIVE_STATUSES = {"open", "partially_filled", "pending"}

        results: list[ProphetOrder] = []
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            node = edge.get("node")
            if not isinstance(node, dict):
                continue
            market_obj = node.get("market") or {}
            node_market_id = (
                market_obj.get("id") if isinstance(market_obj, dict) else None
            ) or ""
            if market_id and node_market_id != market_id:
                continue
            node_status = (node.get("status") or "").lower()
            if wanted_status == "open":
                if node_status not in ACTIVE_STATUSES:
                    continue
            elif wanted_status and node_status != wanted_status:
                continue
            price_bps = node.get("priceBps")
            limit_price = (
                float(price_bps) / 10000.0
                if isinstance(price_bps, (int, float))
                else 0.0
            )
            results.append(
                ProphetOrder(
                    order_id=node.get("id") or "",
                    market_id=node_market_id,
                    outcome=(node.get("outcome") or "").lower(),
                    side=(node.get("side") or "").lower(),
                    shares=_safe_float(node.get("quantityShares")),
                    limit_price=limit_price,
                    filled_shares=_safe_float(node.get("filledShares")),
                    status=node_status,
                )
            )
        return results

    # ------------------------------------------------------------------
    # Writes

    def place_order(
        self,
        *,
        jwt: str,
        market_id: str,
        outcome: str,  # "yes" | "no"
        side: str,  # "buy" | "sell"
        shares: float,
        limit_price: float,
    ) -> ProphetOrder:
        """Submit a LIMIT/GTC order against Prophet's PlaceOrderInput.

        Live schema (probed via tests/fixtures/prophet_schema.json):
            input PlaceOrderInput {
              marketId:    ID!
              outcome:     BetOutcome!  (YES | NO)
              type:        OrderType!   (LIMIT | MARKET)
              side:        OrderSide!   (BUY | SELL)
              priceBps:    Int          (0..10000 basis points)
              quantity:    Float!       (in shares of the outcome token)
              timeInForce: TimeInForce  (GTC | IOC | FOK | DAY)
            }

        `shares` here is the USDC notional the operator intends to commit
        (carried through from arbitrage scoring). We convert to Prophet's
        share-denominated quantity by dividing notional by limit_price.

        Fails closed if:
          - The mutation returns no order (schema mismatch).
          - The GraphQL response carries `errors` (publisher rejected).
          - `limit_price` is outside (0, 1) — Prophet markets are binary.
        """
        if not (0.0 < limit_price < 1.0):
            raise ValueError(
                f"limit_price must be in (0, 1) for binary markets; got {limit_price!r}"
            )
        if outcome not in ("yes", "no"):
            raise ValueError(f"outcome must be 'yes' or 'no'; got {outcome!r}")
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell'; got {side!r}")
        if shares <= 0:
            raise ValueError(f"shares must be > 0; got {shares!r}")

        price_bps = int(round(limit_price * 10000))
        quantity_shares = shares / limit_price

        query = """
        mutation PlaceOrder($input: PlaceOrderInput!) {
          placeOrder(input: $input) {
            order {
              id
              market { id }
              outcome
              side
              type
              priceBps
              quantityShares
              filledShares
              remainingShares
              status
            }
            errors { message }
          }
        }
        """
        payload = self._post(
            jwt=jwt,
            query=query,
            variables={
                "input": {
                    "marketId": market_id,
                    "outcome": outcome.upper(),
                    "type": "LIMIT",
                    "side": side.upper(),
                    "priceBps": price_bps,
                    "quantity": quantity_shares,
                    "timeInForce": "GTC",
                }
            },
        )
        place_payload = ((payload or {}).get("data") or {}).get("placeOrder") or {}
        errors = place_payload.get("errors") or []
        if errors:
            first = errors[0] if isinstance(errors, list) else {}
            message = first.get("message") if isinstance(first, dict) else str(first)
            raise ProphetGraphQLError(f"placeOrder rejected: {message or errors!r}")
        order = place_payload.get("order") or {}
        if not order.get("id"):
            raise ProphetSchemaError(
                "placeOrder did not return an order.id — schema may have drifted "
                "or input shape was rejected. Run agent.py --command probe-schema "
                "and regenerate tests/fixtures/prophet_schema.json."
            )
        market_obj = order.get("market") or {}
        returned_price_bps = order.get("priceBps")
        returned_limit_price = (
            float(returned_price_bps) / 10000.0
            if isinstance(returned_price_bps, (int, float))
            else limit_price
        )
        return ProphetOrder(
            order_id=order.get("id") or "",
            market_id=(market_obj.get("id") if isinstance(market_obj, dict) else None)
            or market_id,
            outcome=(order.get("outcome") or outcome).lower(),
            side=(order.get("side") or side).lower(),
            shares=_safe_float(order.get("quantityShares"), quantity_shares),
            limit_price=returned_limit_price,
            filled_shares=_safe_float(order.get("filledShares")),
            status=(order.get("status") or "open").lower(),
        )

    def cancel_order(self, *, jwt: str, order_id: str) -> bool:
        """Cancel an open order. Returns True if Prophet accepted the cancel.

        Live schema:
            input CancelOrderInput { orderId: ID! }
            type  CancelOrderPayload { order: Order, errors: [...]! }
        """
        query = """
        mutation CancelOrder($input: CancelOrderInput!) {
          cancelOrder(input: $input) {
            order { id status }
            errors { message }
          }
        }
        """
        payload = self._post(
            jwt=jwt,
            query=query,
            variables={"input": {"orderId": order_id}},
        )
        result = ((payload or {}).get("data") or {}).get("cancelOrder") or {}
        errors = result.get("errors") or []
        if errors:
            return False
        order = result.get("order") or {}
        return bool(order.get("id"))

    # ------------------------------------------------------------------
    # transport delegation. Mirrors MinimalProphetClient._post

    def _post(
        self,
        *,
        jwt: str | None,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        response = self.transport.post_graphql(
            jwt=jwt, query=query, variables=variables
        )
        if not isinstance(response, dict):
            raise ProphetSchemaError(
                f"prophet returned non-dict payload: {type(response).__name__}"
            )
        return response


# ---------------------------------------------------------------------------
# Helpers


def _safe_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _parse_outcomes(value: Any) -> tuple[float, float]:
    """Parse Market.outcomes into (yes_price, no_price).

    Tolerates several response shapes:
      [{"name": "Yes", "price": 0.62}, {"name": "No", "price": 0.38}]
      {"yes": 0.62, "no": 0.38}
      [0.62, 0.38]   # legacy positional

    Returns (0.0, 0.0) if shape is unrecognized so callers can flag the
    market as untradable without crashing the whole run.
    """
    if isinstance(value, dict):
        return _safe_float(value.get("yes")), _safe_float(value.get("no"))
    if isinstance(value, list):
        yes = no = 0.0
        for item in value:
            if isinstance(item, dict):
                name = (item.get("name") or "").strip().lower()
                price = _safe_float(item.get("price"))
                if name == "yes":
                    yes = price
                elif name == "no":
                    no = price
            elif isinstance(item, (int, float)):
                if yes == 0.0:
                    yes = float(item)
                elif no == 0.0:
                    no = float(item)
        return yes, no
    return 0.0, 0.0
