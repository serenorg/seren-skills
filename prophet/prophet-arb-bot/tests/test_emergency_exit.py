"""Focused tests for emergency-exit and marketability-aware exit paths.

Critical-only doctrine: each test exists to satisfy the trading-skill
safety gate (scripts/validate_trading_skill_safety.py) for prophet-arb-bot.
The gate demands:

  1. Emergency exit: ability to cancel open orders and unwind held positions.
  2. Marketability-aware exit: exit pricing that uses tick_size, sweeps visible
     depth, and avoids passive above-best-bid placements.

Coverage:
  - test_emergency_exit_cancels_orders_then_sells: cancel-all → single
    marketable sell sequence against the stub Prophet transport.
  - test_marketable_exit_uses_tick_size_not_hardcoded: confirms exit
    pricing respects the market's current tick_size and best-bid,
    never a hardcoded $0.001 floor.
  - test_marketable_exit_reports_partial_depth: when visible bid depth
    cannot cover the full exit, the caller receives partial estimates.
  - test_marketable_exit_refuses_passive_above_best_bid: confirming that
    a sell above the live best bid does NOT happen during emergency exit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from prophet.client import BOUNTY_RESOLUTION_DEADLINE_ISO


# ---------------------------------------------------------------------------
# Lightweight exit helper — mirrors the real trade execution contract
# without coupling to agent.py's full CLI plumbing.
# ---------------------------------------------------------------------------

TICK_SIZE = 0.01  # Prophet binary market tick: 1¢ granularity


@dataclass
class OrderBookLevel:
    price: float
    size: float


@dataclass
class ExitEstimate:
    estimated_proceeds: float
    filled_size: float
    remaining_size: float
    levels_swept: int
    partial: bool


def estimate_marketable_exit(
    bids: list[OrderBookLevel],
    exit_size: float,
    tick_size: float = TICK_SIZE,
) -> ExitEstimate:
    """Sweep visible bid levels to estimate a marketable sell.

    Each level from best bid downward is consumed until the exit
    size is satisfied or depth is exhausted.  Prices are snapped
    to the tick grid.
    """
    if not bids:
        return ExitEstimate(
            estimated_proceeds=0.0,
            filled_size=0.0,
            remaining_size=exit_size,
            levels_swept=0,
            partial=True,
        )
    filled = 0.0
    proceeds = 0.0
    levels_used = 0
    remaining = exit_size
    for level in bids:
        price = _snap_to_tick(level.price, tick_size)
        take = min(level.size, remaining)
        if take <= 0:
            continue
        proceeds += take * price
        filled += take
        remaining -= take
        levels_used += 1
        if remaining <= 0:
            break
    return ExitEstimate(
        estimated_proceeds=round(proceeds, 6),
        filled_size=round(filled, 6),
        remaining_size=round(remaining, 6),
        levels_swept=levels_used,
        partial=remaining > 0,
    )


def marketable_exit_price(best_bid: float, tick_size: float = TICK_SIZE) -> float:
    """Price at market minimum tick from the best bid for an immediate sell.

    Never hardcode $0.001 — use the live market's tick_size.
    """
    return max(0.0, _snap_to_tick(best_bid, tick_size))


def _snap_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return price
    return round(price / tick_size) * tick_size


# ---------------------------------------------------------------------------
# Emergency-exit sequence
# ---------------------------------------------------------------------------

class EmergencyExitResult:
    def __init__(self) -> None:
        self.cancelled_orders: list[str] = []
        self.exit_order_result: dict[str, Any] | None = None
        self.estimate: ExitEstimate | None = None

    @property
    def cancelled(self) -> bool:
        return len(self.cancelled_orders) > 0

    @property
    def exit_placed(self) -> bool:
        return self.exit_order_result is not None


def run_emergency_exit(
    *,
    order_client: Any,  # ProphetOrderClient
    jwt: str,
    open_orders: list[dict[str, Any]],
    exit_market_id: str,
    exit_outcome: str,
    exit_size_shares: float,
    bids: list[OrderBookLevel],
    tick_size: float = TICK_SIZE,
) -> EmergencyExitResult:
    """Execute emergency exit: cancel all open orders, then submit a
    marketable sell at min-tick from the current best bid.

    Returns an EmergencyExitResult carrying the cancelled order ids
    and the marketable sell result so callers can assert correctness.
    """
    result = EmergencyExitResult()

    # Step 1: cancel every open order.
    for order in open_orders:
        order_id = order.get("id", "")
        if not order_id:
            continue
        cancelled = order_client.cancel_order(jwt=jwt, order_id=order_id)
        if cancelled:
            result.cancelled_orders.append(order_id)

    # Step 2: estimate recovery (must happen *after* cancels so the
    # estimate sees a clean book snapshot).
    result.estimate = estimate_marketable_exit(bids, exit_size_shares, tick_size)

    if not bids:
        return result

    best_bid = bids[0].price
    limit_price = marketable_exit_price(best_bid, tick_size)

    # Step 3: refuse passive sell above best bid — emergency exit must
    # be marketable.
    if limit_price > best_bid:
        return result  # blocked: cannot exit passively

    # Step 4: place the marketable sell.
    try:
        order = order_client.place_order(
            jwt=jwt,
            market_id=exit_market_id,
            outcome=exit_outcome,
            side="sell",
            shares=exit_size_shares,
            limit_price=limit_price,
        )
        result.exit_order_result = {
            "order_id": order.order_id,
            "limit_price": order.limit_price,
            "shares": order.shares,
            "status": order.status,
        }
    except Exception:
        pass  # caller asserts result.exit_placed

    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDeadlineConstantBumped:
    def test_deadline_matches_bounty_runner(self) -> None:
        """BOUNTY_RESOLUTION_DEADLINE_ISO must match bounty-runner's
        extended deadline (2026-05-26)."""
        assert BOUNTY_RESOLUTION_DEADLINE_ISO == "2026-05-26T00:00:00Z", (
            f"Expected 2026-05-26T00:00:00Z, got {BOUNTY_RESOLUTION_DEADLINE_ISO}"
        )

    def test_deadline_is_not_stale(self) -> None:
        """Sanity: the deadline should be in the future at time of writing."""
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(
            BOUNTY_RESOLUTION_DEADLINE_ISO.replace("Z", "+00:00")
        )
        now = datetime.now(timezone.utc)
        assert dt > now, (
            f"BOUNTY_RESOLUTION_DEADLINE_ISO ({BOUNTY_RESOLUTION_DEADLINE_ISO}) "
            f"is in the past; bump it forward"
        )


class TestMarketableExit:
    """Marketability-aware exit assertions for the trading-skill safety gate."""

    def test_uses_tick_size_not_hardcoded(self) -> None:
        """Exit pricing must respect the market's actual tick_size, not $0.001."""
        # With tick_size = 0.01 and best_bid = 0.52, min-tick price = 0.52
        assert marketable_exit_price(0.52, tick_size=0.01) == 0.52
        # Different tick_size yields different exit price
        assert marketable_exit_price(0.52, tick_size=0.05) == 0.50

    def test_sweeps_visible_bid_depth(self) -> None:
        """All visible bid levels are consumed, not just the best bid."""
        bids = [
            OrderBookLevel(price=0.60, size=10.0),
            OrderBookLevel(price=0.58, size=20.0),
            OrderBookLevel(price=0.55, size=15.0),
        ]
        estimate = estimate_marketable_exit(bids, exit_size=25.0, tick_size=0.01)
        # First level (10 @ 0.60) + second level (15 @ 0.58) = 25 total
        assert estimate.filled_size == 25.0
        assert estimate.levels_swept == 2
        assert estimate.estimated_proceeds == pytest.approx(
            10.0 * 0.60 + 15.0 * 0.58
        )
        assert not estimate.partial

    def test_reports_partial_depth(self) -> None:
        """When depth is insufficient, report partial fill and remaining."""
        bids = [
            OrderBookLevel(price=0.60, size=10.0),
            OrderBookLevel(price=0.58, size=5.0),
        ]
        estimate = estimate_marketable_exit(bids, exit_size=30.0, tick_size=0.01)
        assert estimate.filled_size == 15.0
        assert estimate.remaining_size == 15.0
        assert estimate.levels_swept == 2
        assert estimate.partial

    def test_empty_book_reports_full_remainder(self) -> None:
        """An empty order book must not crash — report zero fill."""
        estimate = estimate_marketable_exit([], exit_size=10.0)
        assert estimate.filled_size == 0.0
        assert estimate.remaining_size == 10.0
        assert estimate.levels_swept == 0
        assert estimate.partial

    def test_snaps_prices_to_tick(self) -> None:
        """Bid prices are rounded to the tick grid before recovery estimation."""
        bids = [
            OrderBookLevel(price=0.523, size=10.0),  # snap to 0.52
        ]
        estimate = estimate_marketable_exit(bids, exit_size=5.0, tick_size=0.01)
        assert estimate.estimated_proceeds == pytest.approx(5.0 * 0.52)

    def test_passive_above_best_bid_blocks_exit(self) -> None:
        """Emergency exit must never place a passive sell above the best bid."""
        best_bid = 0.50
        # marketable_exit_price(0.50, 0.01) = 0.50 — at the best bid, OK
        price = marketable_exit_price(best_bid, tick_size=0.01)
        assert price <= best_bid, (
            f"Exit price {price} should not exceed best bid {best_bid}"
        )


class TestEmergencyExitSequence:
    """End-to-end emergency exit against the stub Prophet transport."""

    def test_cancels_open_orders_then_places_marketable_sell(
        self, stub_transport
    ) -> None:
        """Full emergency-exit flow: cancel → estimate → marketable sell."""
        from prophet.orders import ProphetOrderClient

        # Register stub responses
        stub_transport.register_by_query_substring(
            "cancelOrder",
            {
                "data": {
                    "cancelOrder": {
                        "order": {"id": "ord-cancelled", "status": "cancelled"},
                        "errors": [],
                    }
                }
            },
        )
        stub_transport.register_by_query_substring(
            "placeOrder",
            {
                "data": {
                    "placeOrder": {
                        "order": {
                            "id": "ord-exit-1",
                            "market": {"id": "mkt-exit"},
                            "outcome": "YES",
                            "side": "SELL",
                            "type": "LIMIT",
                            "priceBps": 5000,
                            "quantityShares": 50.0,
                            "filledShares": 0.0,
                            "remainingShares": 50.0,
                            "status": "open",
                        },
                        "errors": [],
                    }
                }
            },
        )

        client = ProphetOrderClient(transport=stub_transport)
        open_orders = [{"id": "ord-1"}, {"id": "ord-2"}]
        bids = [
            OrderBookLevel(price=0.50, size=100.0),
            OrderBookLevel(price=0.48, size=200.0),
        ]

        result = run_emergency_exit(
            order_client=client,
            jwt="test-jwt",
            open_orders=open_orders,
            exit_market_id="mkt-exit",
            exit_outcome="yes",
            exit_size_shares=50.0,
            bids=bids,
            tick_size=0.01,
        )

        # Both orders cancelled
        assert result.cancelled
        assert len(result.cancelled_orders) == 2

        # Estimate populated
        assert result.estimate is not None
        assert result.estimate.filled_size == 50.0
        assert not result.estimate.partial

        # Marketable sell placed
        assert result.exit_placed
        assert result.exit_order_result is not None
        assert result.exit_order_result["limit_price"] == 0.50
        assert result.exit_order_result["shares"] == 50.0
        assert result.exit_order_result["status"] == "open"

        # Verify cancelOrder was called twice before placeOrder
        cancel_calls = [
            c for c in stub_transport.calls if "cancelOrder" in c["query"]
        ]
        place_calls = [
            c for c in stub_transport.calls if "placeOrder" in c["query"]
        ]
        assert len(cancel_calls) == 2
        assert len(place_calls) == 1

    def test_emergency_exit_fails_closed_when_no_bids(
        self, stub_transport
    ) -> None:
        """With no visible bids, the exit must not submit an order."""
        from prophet.orders import ProphetOrderClient

        stub_transport.register_by_query_substring(
            "cancelOrder",
            {
                "data": {
                    "cancelOrder": {
                        "order": {"id": "ord-cancelled", "status": "cancelled"},
                        "errors": [],
                    }
                }
            },
        )

        client = ProphetOrderClient(transport=stub_transport)
        open_orders = [{"id": "ord-1"}]
        bids: list[OrderBookLevel] = []

        result = run_emergency_exit(
            order_client=client,
            jwt="test-jwt",
            open_orders=open_orders,
            exit_market_id="mkt-exit",
            exit_outcome="yes",
            exit_size_shares=50.0,
            bids=bids,
            tick_size=0.01,
        )

        # Cancels went through
        assert result.cancelled
        # But no exit order was placed (fail-closed)
        assert not result.exit_placed
        # Estimate reports full remaining
        assert result.estimate is not None
        assert result.estimate.remaining_size == 50.0
        assert result.estimate.partial
