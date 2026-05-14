"""Delta-neutral hedge layer for the prophet-arb-bot (#536).

Mode A (single-leg) trades exclusively on Prophet and treats Polymarket
as a fair-value reference. This module adds the opt-in second leg:

  - **Pre-trade depth check.** Before posting a Prophet limit, sweep the
    Polymarket order book at the planned hedge side. If visible depth
    can't cover the target notional at acceptable slippage, the
    opportunity is rejected this cycle. This prevents the "Prophet fills
    but Polymarket can't hedge" failure mode at the source.

  - **Post-fill hedge submission.** When a previously-open Prophet order
    is detected as filled, submit the offsetting Polymarket order at a
    marketable price. The hedge order id + fill metadata propagate back
    to the recorder so the row in `arb_orders` carries the full two-leg
    state.

  - **Hedge-failure path.** If the Polymarket submission throws, the
    hedger records `naked_exposure` and invokes the Prophet unwind
    callback. Prophet's CTF order book has no force-close for the maker
    side once an order has filled, so naked exposure on Prophet is a
    real outcome we have to surface honestly — not paper over.

The module exposes pure functions and a `Hedger` protocol the runner
implements with the real `DirectClobTrader`. Tests use an in-memory
stub. No I/O happens here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Depth check


@dataclass
class DepthAssessment:
    """Verdict from sweeping the Polymarket book for a planned hedge."""

    sufficient: bool
    target_size_usdc: float
    fillable_size_usdc: float
    average_price: float
    realized_slippage_bps: float
    max_slippage_bps: float
    reason: str  # "ok" | "insufficient_depth" | "excess_slippage" | "no_liquidity"


def _book_levels(payload: Any, hedge_side: str) -> list[tuple[float, float]]:
    """Return ordered (price, size) levels for the side the hedge will hit.

    Hedge side -> book side traversed:
      - hedge sells YES on Polymarket → consume bids from highest to lowest
      - hedge buys  YES on Polymarket → consume asks from lowest to highest

    The polymarket-data publisher returns levels under `raw.bids` /
    `raw.asks` as list-of-dict with `price`/`size` (strings). We coerce
    to floats and skip malformed rows silently — depth filtering happens
    in the caller.
    """
    raw = payload.get("raw") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return []
    key = "bids" if hedge_side.lower() == "sell" else "asks"
    rows = raw.get(key) or []
    out: list[tuple[float, float]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            price = float(row.get("price", 0.0))
            size = float(row.get("size", 0.0))
        except (TypeError, ValueError):
            continue
        if price <= 0.0 or size <= 0.0:
            continue
        out.append((price, size))
    # Sort: bids high-to-low (hedge sell consumes best bid first),
    # asks low-to-high (hedge buy consumes best ask first).
    out.sort(key=lambda lvl: lvl[0], reverse=(key == "bids"))
    return out


def assess_polymarket_depth(
    *,
    book_payload: dict[str, Any],
    target_size_usdc: float,
    hedge_side: str,
    max_slippage_bps: float = 200.0,
) -> DepthAssessment:
    """Pure function. Given a polymarket-data book snapshot, decide
    whether the visible depth can absorb a hedge of ``target_size_usdc``
    without walking the book past ``max_slippage_bps`` from the best
    level.

    ``hedge_side`` is the side the Polymarket hedge will take —
    ``"sell"`` if we're buying YES on Prophet (and need to sell YES
    elsewhere to hedge), ``"buy"`` if we're selling YES on Prophet.

    Sizing is measured in **USDC notional** (price × size summed over
    consumed levels), which is what the Prophet leg uses for collateral.
    """
    levels = _book_levels(book_payload, hedge_side)
    if not levels:
        return DepthAssessment(
            sufficient=False,
            target_size_usdc=target_size_usdc,
            fillable_size_usdc=0.0,
            average_price=0.0,
            realized_slippage_bps=0.0,
            max_slippage_bps=max_slippage_bps,
            reason="no_liquidity",
        )

    best_price = levels[0][0]
    remaining_usdc = target_size_usdc
    gross_value = 0.0
    filled_shares = 0.0
    for price, size in levels:
        level_value_usdc = price * size
        take_usdc = min(remaining_usdc, level_value_usdc)
        gross_value += take_usdc
        filled_shares += take_usdc / price
        remaining_usdc -= take_usdc
        if remaining_usdc <= 1e-9:
            break

    fillable_usdc = round(gross_value, 6)
    average_price = gross_value / filled_shares if filled_shares > 0 else 0.0

    if fillable_usdc + 1e-6 < target_size_usdc:
        return DepthAssessment(
            sufficient=False,
            target_size_usdc=target_size_usdc,
            fillable_size_usdc=fillable_usdc,
            average_price=round(average_price, 6),
            realized_slippage_bps=0.0,
            max_slippage_bps=max_slippage_bps,
            reason="insufficient_depth",
        )

    # Slippage is measured in basis points of price drift from best.
    # For SELL hedges (consuming bids), price moves DOWN from best_bid;
    # for BUY hedges (consuming asks), price moves UP from best_ask.
    # Either way, |average − best| / best × 10000 is the magnitude.
    slippage_bps = abs(average_price - best_price) / best_price * 10_000.0
    if slippage_bps > max_slippage_bps:
        return DepthAssessment(
            sufficient=False,
            target_size_usdc=target_size_usdc,
            fillable_size_usdc=fillable_usdc,
            average_price=round(average_price, 6),
            realized_slippage_bps=round(slippage_bps, 2),
            max_slippage_bps=max_slippage_bps,
            reason="excess_slippage",
        )

    return DepthAssessment(
        sufficient=True,
        target_size_usdc=target_size_usdc,
        fillable_size_usdc=fillable_usdc,
        average_price=round(average_price, 6),
        realized_slippage_bps=round(slippage_bps, 2),
        max_slippage_bps=max_slippage_bps,
        reason="ok",
    )


# ---------------------------------------------------------------------------
# Hedge submission


@dataclass
class HedgeOutcome:
    """Result of attempting to hedge a filled Prophet order.

    ``hedge_status`` aligns with the new column on ``arb_orders``:
      - ``hedged``: Polymarket order accepted, prophet+poly legs balanced.
      - ``naked_exposure``: Polymarket order rejected; Prophet leg is
        filled and unhedgeable on the maker side. Operator must liquidate
        the Prophet position manually via the UI.
      - ``unwound``: Polymarket order rejected but Prophet order was
        still open and was cancelled before fill. Clean exit.
      - ``hedge_failed_no_commit``: seed hedge failed before Prophet
        Confirm, so the agent must not commit the Prophet seed.
      - ``unwound_after_prophet_decline``: seed hedge filled first, then
        Prophet Confirm failed/declined, and the Polymarket leg was
        marketably reversed.
    """

    hedge_status: str
    polymarket_order_id: str | None
    polymarket_filled_qty: float
    polymarket_fill_price: float
    error: str | None = None


class Hedger(Protocol):
    """Interface between the runner and the live Polymarket CLOB.

    Production impl wraps ``DirectClobTrader`` from
    ``scripts/polymarket_live.py``. Tests pass an in-memory stub.
    """

    def submit_hedge(
        self,
        *,
        condition_id: str,
        hedge_side: str,
        size_usdc: float,
        marketable_price: float,
    ) -> dict[str, Any]:
        """Submit a marketable limit hedge. Returns
        ``{polymarket_order_id, filled_qty, fill_price}``. Raises on
        failure — the caller catches and routes to the unwind path."""
        ...

    def unwind_prophet(self, *, order_id: str) -> None:
        """Cancel the Prophet order if still cancellable. If already
        filled, this is a no-op on the cancel side — the naked exposure
        is surfaced in `HedgeOutcome` for operator follow-up."""
        ...


def _opposite_side(prophet_side: str) -> str:
    """Hedge side = opposite of the Prophet side.

    If we bought YES on Prophet we need to sell YES on Polymarket to
    flatten net exposure; if we sold YES on Prophet we need to buy YES
    on Polymarket. The arb-bot trades only the YES leg (binary symmetry
    keeps the NO leg out of the surface)."""
    if prophet_side.lower() == "buy":
        return "sell"
    if prophet_side.lower() == "sell":
        return "buy"
    raise ValueError(f"unknown prophet side: {prophet_side!r}")


def hedge_filled_order(
    *,
    prophet_order: Any,
    polymarket_condition_id: str,
    hedger: Hedger,
    marketable_price: float,
) -> HedgeOutcome:
    """Submit the offsetting Polymarket order for a filled Prophet order.

    Failure routing:
      - The hedger raises → unwind Prophet (cancel) and return
        ``naked_exposure`` so the recorder persists the half-filled state.
      - The hedger returns a result with a falsy ``polymarket_order_id``
        → same path; this shouldn't happen with the real CLOB but the
        guard is cheap and keeps the contract tight.
    """
    hedge_side = _opposite_side(prophet_order.side)
    # Naming wart pinned in agent.py:475 — `place_order(shares=opp.size_usdc)`
    # carries USDC notional in the `shares` field, not a share count. So
    # `filled_shares` returned by Prophet's GraphQL is already a USDC
    # notional, and the hedge consumes the same notional on Polymarket.
    filled_size_usdc = float(prophet_order.filled_shares)
    if filled_size_usdc <= 0.0:
        filled_size_usdc = float(prophet_order.shares)

    try:
        result = hedger.submit_hedge(
            condition_id=polymarket_condition_id,
            hedge_side=hedge_side,
            size_usdc=filled_size_usdc,
            marketable_price=marketable_price,
        )
    except Exception as exc:
        # Hedge dead on arrival — try to cancel Prophet (no-op if filled).
        try:
            hedger.unwind_prophet(order_id=prophet_order.order_id)
        except Exception:
            # Even the unwind failed; surface naked exposure with the
            # original submit error preserved so the operator sees it.
            pass
        return HedgeOutcome(
            hedge_status="naked_exposure",
            polymarket_order_id=None,
            polymarket_filled_qty=0.0,
            polymarket_fill_price=0.0,
            error=str(exc)[:200],
        )

    polymarket_order_id = result.get("polymarket_order_id") if isinstance(result, dict) else None
    if not polymarket_order_id:
        # CLOB returned without an id — treat as soft-failure, attempt unwind.
        try:
            hedger.unwind_prophet(order_id=prophet_order.order_id)
        except Exception:
            pass
        return HedgeOutcome(
            hedge_status="naked_exposure",
            polymarket_order_id=None,
            polymarket_filled_qty=0.0,
            polymarket_fill_price=0.0,
            error="polymarket_submit_returned_no_order_id",
        )

    return HedgeOutcome(
        hedge_status="hedged",
        polymarket_order_id=str(polymarket_order_id),
        polymarket_filled_qty=float(result.get("filled_qty", 0.0)),
        polymarket_fill_price=float(result.get("fill_price", 0.0)),
        error=None,
    )


# ---------------------------------------------------------------------------
# Seed-bet hedge (#542 Fix 3)


def hedge_seed_bet(
    *,
    prophet_market_id: str = "",
    polymarket_condition_id: str,
    prophet_seed_side: str,
    size_usdc: float,
    marketable_price: float,
    hedger: Hedger,
) -> HedgeOutcome:
    """Submit the opposing Polymarket order for a Prophet Phase 15 seed.

    Differs from ``hedge_filled_order`` in one critical way: the Prophet
    seed has **not** been committed yet. This function runs before the
    agent clicks Confirm in Prophet's in-browser signing prompt. If the
    Polymarket hedge fails, the agent aborts the Prophet confirm and
    there is no exposure on either venue.

    ``prophet_seed_side`` is the side the operator bet on Prophet
    (``"buy"`` for YES, ``"sell"`` for NO). The hedge takes the
    opposite side on Polymarket.
    """
    hedge_side = _opposite_side(prophet_seed_side)

    try:
        result = hedger.submit_hedge(
            condition_id=polymarket_condition_id,
            hedge_side=hedge_side,
            size_usdc=float(size_usdc),
            marketable_price=float(marketable_price),
        )
    except Exception as exc:
        return HedgeOutcome(
            hedge_status="hedge_failed_no_commit",
            polymarket_order_id=None,
            polymarket_filled_qty=0.0,
            polymarket_fill_price=0.0,
            error=str(exc)[:200],
        )

    polymarket_order_id = (
        result.get("polymarket_order_id") if isinstance(result, dict) else None
    )
    if not polymarket_order_id:
        return HedgeOutcome(
            hedge_status="hedge_failed_no_commit",
            polymarket_order_id=None,
            polymarket_filled_qty=0.0,
            polymarket_fill_price=0.0,
            error="polymarket_submit_returned_no_order_id",
        )

    return HedgeOutcome(
        hedge_status="hedged",
        polymarket_order_id=str(polymarket_order_id),
        polymarket_filled_qty=float(result.get("filled_qty", 0.0)),
        polymarket_fill_price=float(result.get("fill_price", 0.0)),
        error=None,
    )


def unwind_seed_hedge_after_prophet_decline(
    *,
    polymarket_condition_id: str,
    prophet_seed_side: str,
    size_usdc: float,
    marketable_price: float,
    hedger: Hedger,
) -> HedgeOutcome:
    """Reverse a seed hedge when Prophet Confirm fails after Polymarket
    already filled.

    Polymarket is the recoverable leg. The original seed hedge used the
    opposite of ``prophet_seed_side``; the unwind therefore submits the
    same side as the intended Prophet seed.
    """
    unwind_side = prophet_seed_side.lower()
    if unwind_side not in {"buy", "sell"}:
        raise ValueError(f"unknown prophet side: {prophet_seed_side!r}")

    try:
        result = hedger.submit_hedge(
            condition_id=polymarket_condition_id,
            hedge_side=unwind_side,
            size_usdc=float(size_usdc),
            marketable_price=float(marketable_price),
        )
    except Exception as exc:
        return HedgeOutcome(
            hedge_status="naked_exposure",
            polymarket_order_id=None,
            polymarket_filled_qty=0.0,
            polymarket_fill_price=0.0,
            error=str(exc)[:200],
        )

    polymarket_order_id = (
        result.get("polymarket_order_id") if isinstance(result, dict) else None
    )
    if not polymarket_order_id:
        return HedgeOutcome(
            hedge_status="naked_exposure",
            polymarket_order_id=None,
            polymarket_filled_qty=0.0,
            polymarket_fill_price=0.0,
            error="polymarket_unwind_returned_no_order_id",
        )

    return HedgeOutcome(
        hedge_status="unwound_after_prophet_decline",
        polymarket_order_id=str(polymarket_order_id),
        polymarket_filled_qty=float(result.get("filled_qty", 0.0)),
        polymarket_fill_price=float(result.get("fill_price", 0.0)),
        error=None,
    )


# ---------------------------------------------------------------------------
# Per-market seed-intent decision (#548)


@dataclass(frozen=True)
class SeedIntent:
    """Side + marketable price for a delta-neutral seed bet.

    The arb-bot's seed thesis: buy the YES leg on the venue that
    underprices it, hedge with the opposite side on the other venue.
    `seed_side` is the side the operator takes on Prophet (the YES leg);
    `hedge_side` is the opposite side the Polymarket leg fills. Edge is
    reported in basis points of probability so the agent can compare
    against `scoring.min_spread` without unit gymnastics.
    """

    seed_side: str
    hedge_side: str
    hedge_price: float
    tick_size: str
    edge_bps: float


def _snap_to_tick(price: float, tick_size: str, side: str) -> float:
    """Match `polymarket_live.snap_price` semantics without the import.

    BUY hedges hit asks → floor to current tick (we won't pay above ask).
    SELL hedges hit bids → ceil to current tick (we won't sell below bid).
    Result is clamped to `[tick, 1 - tick]` so it stays a valid CLOB
    price and rounded to the tick's decimal precision.
    """
    import math

    try:
        tick = float(tick_size)
    except (TypeError, ValueError):
        tick = 0.01
    if tick <= 0.0:
        tick = 0.01
    lower = tick
    upper = 1.0 - tick
    normalized = max(lower, min(upper, price))
    ratio = normalized / tick
    if side.upper() == "BUY":
        snapped = math.floor(ratio) * tick
    else:
        snapped = math.ceil(ratio) * tick
    decimals = len(tick_size.split(".")[1]) if "." in tick_size else 0
    return round(max(lower, min(upper, snapped)), max(decimals, 0))


def derive_seed_intent(
    *,
    prophet_fair_value_bps: int,
    polymarket_yes_price: float,
    book_payload: dict[str, Any],
    min_edge_bps: float = 0.0,
) -> SeedIntent | None:
    """Decide the seed/hedge sides from Prophet's AI fair value vs the
    live Polymarket YES price. Returns None when there is no actionable
    edge or the Polymarket book lacks the required side.

    Rule:
      - Prophet fair value > Polymarket price → Prophet underprices YES;
        operator buys YES on Prophet, hedge sells YES on Polymarket at
        best-bid snapped to tick.
      - Prophet fair value < Polymarket price → Polymarket underprices NO;
        operator sells YES on Prophet (== buys NO), hedge buys YES on
        Polymarket at best-ask snapped to tick.

    Edge is the absolute probability gap in bps. The function does not
    apply a slippage budget — `assess_polymarket_depth` already does
    that with `max_hedge_slippage_bps` in the preflight loop.
    """
    prophet_yes = max(0.0, min(1.0, prophet_fair_value_bps / 10_000.0))
    poly_yes = max(0.0, min(1.0, float(polymarket_yes_price)))
    delta = prophet_yes - poly_yes
    edge_bps = abs(delta) * 10_000.0
    if edge_bps <= min_edge_bps:
        return None

    tick_size = str(book_payload.get("tick_size") or "0.01")
    if delta > 0:
        # Prophet says YES is more likely than Polymarket.
        best_bid = float(book_payload.get("best_bid") or 0.0)
        if best_bid <= 0.0:
            return None
        hedge_price = _snap_to_tick(best_bid, tick_size, "SELL")
        return SeedIntent(
            seed_side="buy",
            hedge_side="sell",
            hedge_price=hedge_price,
            tick_size=tick_size,
            edge_bps=edge_bps,
        )

    # delta < 0 — Prophet says YES is less likely than Polymarket.
    best_ask = float(book_payload.get("best_ask") or 0.0)
    if best_ask <= 0.0:
        return None
    hedge_price = _snap_to_tick(best_ask, tick_size, "BUY")
    return SeedIntent(
        seed_side="sell",
        hedge_side="buy",
        hedge_price=hedge_price,
        tick_size=tick_size,
        edge_bps=edge_bps,
    )
