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
    max_slippage_bps: float = 100.0,
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

    #722: ``failure`` carries the structured diagnostic on terminal
    failure (None on success). Keys: ``error_class``, ``clob_http_status``,
    ``clob_error_message``, ``exception_type``, ``exception_message``,
    ``submitted_order``, ``attempts``. ``attempts`` is also lifted to the
    top level so success rows can render the retry count without digging
    into ``failure``.
    """

    hedge_status: str
    polymarket_order_id: str | None
    polymarket_filled_qty: float
    polymarket_fill_price: float
    error: str | None = None
    failure: dict[str, Any] | None = None
    attempts: int = 1


# ---------------------------------------------------------------------------
# #722 — Hedge failure classifier
#
# Single source of truth for retry routing and operator-facing failure
# messages. Pure function: takes an exception, returns one of the enum
# strings. CLOB substrings drive class assignment because that is the
# only durable signal Polymarket exposes — they do not publish stable
# numeric error codes for the CLOB rejections.


HedgeErrorClass = str  # one of the literals below

HEDGE_ERROR_CLASSES: tuple[str, ...] = (
    "insufficient_funds",
    "market_unavailable",
    "invalid_params",
    "allowance_revoked",
    "transient_clob_error",
    "book_moved",
    "region_blocked",
    "unknown",
)


_INSUFFICIENT_FUNDS_TOKENS = (
    "insufficient_balance",
    "insufficient balance",
    "insufficient_funds",
    "not enough collateral",
    "balance too low",
)
_MARKET_UNAVAILABLE_TOKENS = (
    "market_not_open",
    "market not open",
    "market_closed",
    "market closed",
    "market_resolved",
    "market resolved",
    "market_paused",
    "market paused",
    "market not active",
    "not_active",
)
_INVALID_PARAMS_TOKENS = (
    "tick_size",
    "tick size",
    "invalid_order",
    "invalid order",
    "invalid_price",
    "invalid price",
    "invalid_size",
    "invalid size",
    "signature_invalid",
    "invalid_signature",
)
_ALLOWANCE_TOKENS = (
    "allowance",
    "approve_spender",
    "not_approved",
    "not approved",
)
_BOOK_MOVED_TOKENS = (
    "not_enough_marketable_liquidity",
    "marketable_liquidity",
    "would_cross_book",
    "price_outside_book",
)
# #730: Polymarket geoblock 403 phrasing. Pinned verbatim against the
# live CLOB message captured today; the docs URL substring is the most
# stable signal (the human-readable prefix can shift). Tokens are
# matched case-insensitively against the lower-cased exception body.
_REGION_BLOCKED_TOKENS = (
    "trading restricted",
    "restricted in your region",
    "geoblock",
    "geo-block",
    "available regions",
    "clob/geoblock",  # the docs URL fragment — most schema-stable
)
_TRANSIENT_EXCEPTION_TYPES: tuple[type, ...] = (
    TimeoutError,
    ConnectionError,
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionRefusedError,
)


def classify_hedge_failure(exc: BaseException) -> HedgeErrorClass:
    """Map a hedge submission exception into one of ``HEDGE_ERROR_CLASSES``.

    Recognizes ``py-clob-client.exceptions.PolyApiException`` via its
    ``status_code`` + ``error_msg`` attributes (duck-typed; we don't
    import py-clob-client here so tests can supply a fake without the
    library installed). Network/timeout exceptions are transient. Anything
    that doesn't match a known token bucket falls into ``unknown`` so the
    operator still gets the structured payload but the bot won't auto-retry.
    """
    status_code = getattr(exc, "status_code", None)
    error_msg = getattr(exc, "error_msg", None)

    # Normalize the body to a lower-case search string. Use the python
    # exception's repr as a fallback because some clients raise
    # ``RequestException("503: Service Unavailable")`` without setting
    # ``error_msg`` directly.
    body = ""
    if isinstance(error_msg, str):
        body = error_msg
    elif error_msg is not None:
        try:
            body = str(error_msg)
        except Exception:
            body = ""
    if not body:
        try:
            body = str(exc)
        except Exception:
            body = ""
    body_lc = body.lower()

    # #730: region-blocked is checked before the generic 4xx/5xx routing
    # below so a 403 carrying the geoblock phrasing routes to the
    # operator-actionable `region_blocked` class instead of being swept
    # into `unknown` (or, if the message also happens to contain a 5xx
    # token, into transient retry).
    if any(token in body_lc for token in _REGION_BLOCKED_TOKENS):
        return "region_blocked"
    if any(token in body_lc for token in _INSUFFICIENT_FUNDS_TOKENS):
        return "insufficient_funds"
    if any(token in body_lc for token in _MARKET_UNAVAILABLE_TOKENS):
        return "market_unavailable"
    if any(token in body_lc for token in _ALLOWANCE_TOKENS):
        return "allowance_revoked"
    if any(token in body_lc for token in _BOOK_MOVED_TOKENS):
        return "book_moved"
    if any(token in body_lc for token in _INVALID_PARAMS_TOKENS):
        return "invalid_params"

    # 5xx + connection-level errors are transient regardless of body.
    if isinstance(status_code, int) and 500 <= status_code < 600:
        return "transient_clob_error"
    if isinstance(exc, _TRANSIENT_EXCEPTION_TYPES):
        return "transient_clob_error"
    if "timed out" in body_lc or "timeout" in body_lc:
        return "transient_clob_error"
    if "service unavailable" in body_lc or "temporarily unavailable" in body_lc:
        return "transient_clob_error"

    return "unknown"


def _build_failure_payload(
    *,
    exc: BaseException,
    submitted_order: dict[str, Any],
    attempts: int,
) -> dict[str, Any]:
    """Assemble the structured failure diagnostic that lands in
    ``HedgeOutcome.failure`` and gets surfaced to the run envelope,
    progress stream, and ``arb_orders`` row."""
    error_class = classify_hedge_failure(exc)
    status_code = getattr(exc, "status_code", None)
    error_msg = getattr(exc, "error_msg", None)
    try:
        exception_message = str(exc)
    except Exception:
        exception_message = ""
    return {
        "error_class": error_class,
        "clob_http_status": status_code if isinstance(status_code, int) else None,
        "clob_error_message": error_msg if isinstance(error_msg, str) else None,
        "exception_type": type(exc).__name__,
        "exception_message": exception_message[:500],
        "submitted_order": dict(submitted_order),
        "attempts": attempts,
    }


# Retry policy: only ``transient_clob_error`` triggers auto-retry inside
# the same submit attempt. Bounded at 3 total attempts (initial + 2
# retries). Backoff is small and synchronous because the agent has not
# yet clicked Prophet Confirm — no naked exposure exists during the
# wait, just delay before either succeeding or surfacing the diagnostic.
HEDGE_TRANSIENT_RETRY_BUDGET = 3
HEDGE_TRANSIENT_RETRY_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0)


class Hedger(Protocol):
    """Interface between the runner and the live Polymarket CLOB.

    Production impl wraps ``DirectClobTrader`` from
    ``scripts/polymarket_live.py``. Tests pass an in-memory stub.
    """

    def submit_hedge(
        self,
        *,
        token_id: str,
        hedge_side: str,
        size_usdc: float,
        marketable_price: float,
    ) -> dict[str, Any]:
        """Submit a marketable limit hedge. Returns
        ``{polymarket_order_id, filled_qty, fill_price}``. Raises on
        failure — the caller catches and routes to the unwind path.

        #631: ``token_id`` is the uint256 decimal YES outcome token_id
        from Polymarket Gamma's ``clobTokenIds[0]``. The CLOB rejects
        condition_ids passed to ``create_order(token_id=…)``; the
        parameter is named ``token_id`` to make that contract
        structural — callers can no longer silently confuse the two.
        """
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
    polymarket_yes_token_id: str,
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

    #631: ``polymarket_condition_id`` is the pair-identity (used by the
    recorder and persistence); ``polymarket_yes_token_id`` is the
    uint256-decimal YES outcome token_id from Gamma's clobTokenIds and
    is what the CLOB actually needs for ``create_order``. The two are
    distinct identifiers; passing one for the other silently fails.
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
            token_id=polymarket_yes_token_id,
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
    polymarket_yes_token_id: str,
    prophet_seed_side: str,
    size_usdc: float,
    marketable_price: float,
    hedger: Hedger,
    _sleep: Any = None,
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

    #631: ``polymarket_yes_token_id`` is the uint256-decimal YES outcome
    token_id the CLOB requires for ``create_order``; condition_id is the
    pair-identity used by the recorder and persistence.

    #722: failures return a structured ``HedgeOutcome.failure`` payload
    (CLOB status code + response body + submitted params + attempts).
    Transient CLOB errors auto-retry up to ``HEDGE_TRANSIENT_RETRY_BUDGET``
    times because the Prophet leg is still uncommitted — no naked
    exposure can exist during the retry wait. All other classes fail
    closed on the first rejection so the operator sees the actual cause.
    """
    import time as _time

    sleep_fn = _sleep if _sleep is not None else _time.sleep

    hedge_side = _opposite_side(prophet_seed_side)
    submitted_order = {
        "token_id": polymarket_yes_token_id,
        "hedge_side": hedge_side,
        "size_usdc": float(size_usdc),
        "marketable_price": float(marketable_price),
    }

    last_exc: BaseException | None = None
    attempts = 0
    result: Any = None
    for attempt_index in range(HEDGE_TRANSIENT_RETRY_BUDGET):
        attempts = attempt_index + 1
        try:
            result = hedger.submit_hedge(
                token_id=polymarket_yes_token_id,
                hedge_side=hedge_side,
                size_usdc=float(size_usdc),
                marketable_price=float(marketable_price),
            )
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            error_class = classify_hedge_failure(exc)
            if error_class != "transient_clob_error":
                # Fail closed immediately on non-transient classes —
                # auto-retry would burn cycles and the next cron tick
                # will reattempt naturally if the cause clears.
                break
            if attempt_index + 1 >= HEDGE_TRANSIENT_RETRY_BUDGET:
                # Budget exhausted; surface the last transient failure.
                break
            backoff_idx = min(
                attempt_index, len(HEDGE_TRANSIENT_RETRY_BACKOFF_SECONDS) - 1
            )
            sleep_fn(HEDGE_TRANSIENT_RETRY_BACKOFF_SECONDS[backoff_idx])

    if last_exc is not None:
        failure = _build_failure_payload(
            exc=last_exc,
            submitted_order=submitted_order,
            attempts=attempts,
        )
        return HedgeOutcome(
            hedge_status="hedge_failed_no_commit",
            polymarket_order_id=None,
            polymarket_filled_qty=0.0,
            polymarket_fill_price=0.0,
            error=str(last_exc)[:200],
            failure=failure,
            attempts=attempts,
        )

    polymarket_order_id = (
        result.get("polymarket_order_id") if isinstance(result, dict) else None
    )
    if not polymarket_order_id:
        # CLOB returned without an id — synthesize a failure payload so
        # the operator sees the same structured shape as a raised error.
        synthetic = RuntimeError("polymarket_submit_returned_no_order_id")
        failure = _build_failure_payload(
            exc=synthetic,
            submitted_order=submitted_order,
            attempts=attempts,
        )
        return HedgeOutcome(
            hedge_status="hedge_failed_no_commit",
            polymarket_order_id=None,
            polymarket_filled_qty=0.0,
            polymarket_fill_price=0.0,
            error="polymarket_submit_returned_no_order_id",
            failure=failure,
            attempts=attempts,
        )

    return HedgeOutcome(
        hedge_status="hedged",
        polymarket_order_id=str(polymarket_order_id),
        polymarket_filled_qty=float(result.get("filled_qty", 0.0)),
        polymarket_fill_price=float(result.get("fill_price", 0.0)),
        error=None,
        failure=None,
        attempts=attempts,
    )


def unwind_seed_hedge_after_prophet_decline(
    *,
    polymarket_condition_id: str,
    polymarket_yes_token_id: str,
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

    #631: see ``hedge_seed_bet`` for the condition_id vs token_id
    contract — the unwind needs the same token_id the original hedge
    submitted with.
    """
    unwind_side = prophet_seed_side.lower()
    if unwind_side not in {"buy", "sell"}:
        raise ValueError(f"unknown prophet side: {prophet_seed_side!r}")

    try:
        result = hedger.submit_hedge(
            token_id=polymarket_yes_token_id,
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
