"""Prophet order operations — best-guess GraphQL for placeOrder / cancelOrder.

prophet-bounty-runner only ships market creation. The arb-bot needs the
trading half: place limit orders, cancel them, list outstanding orders,
and read live odds. The exact GraphQL shape for these mutations has not
been introspected yet (see plan §3 ADR — `prophet_order_schema.json`
fixture is a follow-on PR).

Until the fixture lands, every mutation here is a *best guess* derived
from prophet's published web app behavior:

  - `placeOrder(input: PlaceOrderInput!)` mirrors the four-step
    `createMarketWithBet` shape used by the bounty-runner: a single
    input object carrying market id, outcome, side, shares, limit price.
  - `cancelOrder(orderId: ID!)` follows the standard CRUD-mutation shape.
  - `userOrders(marketId: ID, status: OrderStatus)` mirrors `markets()`
    in the bounty-runner.

If the live schema rejects any of these calls, the gateway raises
`ProphetGraphQLError` with the GraphQL `errors[0].message`. Callers must
treat that as a hard fail-closed signal — do NOT retry with a different
shape blindly. The probe-schema CLI flag in agent.py captures the live
introspection so the next revision can pin field names exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import ProphetGraphQLError, ProphetSchemaError
from .client import GRAPHQL_PATH, PUBLISHER


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
    """Order operations against prophet-ai.

    Composition over inheritance: takes the same `gateway` seam used by
    `MinimalProphetClient` so tests can swap a stub. The two clients
    share `PUBLISHER` and `GRAPHQL_PATH` constants so any future
    publisher rename only needs one change.
    """

    def __init__(self, *, gateway: Any) -> None:
        self.gateway = gateway

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
        """User's outstanding orders (open by default).

        Schema guess: prophet-ai exposes `userOrders` as a viewer-scoped
        query with optional market and status filters. If the live schema
        names this `viewer.orders` instead, the GraphQL error from the
        gateway will identify the mismatch and the operator can swap.
        """
        query = """
        query UserOrders($marketId: ID, $status: OrderStatus) {
          userOrders(marketId: $marketId, status: $status) {
            id
            marketId
            outcome
            side
            shares
            limitPrice
            filledShares
            status
          }
        }
        """
        variables: dict[str, Any] = {}
        if market_id:
            variables["marketId"] = market_id
        if status:
            variables["status"] = status
        payload = self._post(jwt=jwt, query=query, variables=variables)
        orders = ((payload or {}).get("data") or {}).get("userOrders") or []
        if not isinstance(orders, list):
            raise ProphetSchemaError(
                "userOrders did not return a list — schema may have drifted"
            )
        return [
            ProphetOrder(
                order_id=o.get("id") or "",
                market_id=o.get("marketId") or "",
                outcome=(o.get("outcome") or "").lower(),
                side=(o.get("side") or "").lower(),
                shares=_safe_float(o.get("shares")),
                limit_price=_safe_float(o.get("limitPrice")),
                filled_shares=_safe_float(o.get("filledShares")),
                status=(o.get("status") or "").lower(),
            )
            for o in orders
            if isinstance(o, dict)
        ]

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
        """Submit a limit order. Returns the order record on success.

        Schema guess (best-guess submitter — §3 ADR):
            mutation placeOrder($input: PlaceOrderInput!) {
              placeOrder(input: $input) {
                order { id marketId outcome side shares limitPrice
                        filledShares status }
              }
            }

        Input shape mirrors the four-step `createMarketWithBet` pattern
        from the bounty-runner. Limit price is a 0-1 probability; shares
        is the integer-or-float quantity (USDC value at fill).

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

        query = """
        mutation PlaceOrder($input: PlaceOrderInput!) {
          placeOrder(input: $input) {
            order {
              id
              marketId
              outcome
              side
              shares
              limitPrice
              filledShares
              status
            }
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
                    "side": side.upper(),
                    "shares": shares,
                    "limitPrice": limit_price,
                }
            },
        )
        order = (
            ((payload or {}).get("data") or {})
            .get("placeOrder", {})
            .get("order")
            or {}
        )
        if not order.get("id"):
            raise ProphetSchemaError(
                "placeOrder did not return an order.id — schema may have drifted "
                "or input shape was rejected. Run agent.py --probe-schema and "
                "regenerate tests/fixtures/prophet_orders_schema.json."
            )
        return ProphetOrder(
            order_id=order.get("id") or "",
            market_id=order.get("marketId") or market_id,
            outcome=(order.get("outcome") or outcome).lower(),
            side=(order.get("side") or side).lower(),
            shares=_safe_float(order.get("shares"), shares),
            limit_price=_safe_float(order.get("limitPrice"), limit_price),
            filled_shares=_safe_float(order.get("filledShares")),
            status=(order.get("status") or "open").lower(),
        )

    def cancel_order(self, *, jwt: str, order_id: str) -> bool:
        """Cancel an open order. Returns True on success."""
        query = """
        mutation CancelOrder($orderId: ID!) {
          cancelOrder(orderId: $orderId) {
            ok
          }
        }
        """
        payload = self._post(jwt=jwt, query=query, variables={"orderId": order_id})
        result = ((payload or {}).get("data") or {}).get("cancelOrder") or {}
        return bool(result.get("ok"))

    # ------------------------------------------------------------------
    # transport — uniform error handling. Mirrors MinimalProphetClient._post

    def _post(
        self,
        *,
        jwt: str | None,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        if jwt:
            headers["Authorization"] = f"Bearer {jwt}"

        response = self.gateway.call(
            PUBLISHER,
            "POST",
            GRAPHQL_PATH,
            body={"query": query, "variables": variables},
            headers=headers,
        )

        if not isinstance(response, dict):
            raise ProphetSchemaError(
                f"prophet-ai returned non-dict payload: {type(response).__name__}"
            )

        errors = response.get("errors")
        if errors:
            first = errors[0] if isinstance(errors, list) and errors else {}
            message = (
                first.get("message") if isinstance(first, dict) else str(first)
            ) or "unknown GraphQL error"
            raise ProphetGraphQLError(f"prophet-ai GraphQL errors: {message}")

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
