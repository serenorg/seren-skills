"""Event-driven stateful pair replay for Kalshi basis-maker backtest.

Prices from Kalshi are in CENTS (1-99). Internally we normalize to the 0.01-0.99
decimal range for consistent basis-bps arithmetic and fill simulation, then
convert back when reporting notional USD (1 contract = $1 at resolution).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import pstdev
from typing import Any


@dataclass(frozen=True)
class OrderBookSnapshot:
    t: int
    best_bid: float  # decimal 0-1
    best_ask: float  # decimal 0-1
    bid_size_usd: float
    ask_size_usd: float


@dataclass(frozen=True)
class PairReplayParams:
    bankroll_usd: float
    basis_entry_bps: float
    basis_exit_bps: float
    min_edge_bps: float
    expected_unwind_cost_bps: float
    expected_convergence_ratio: float
    base_pair_notional_usd: float
    max_notional_per_pair_usd: float
    max_total_notional_usd: float
    max_leg_notional_usd: float
    participation_rate: float
    min_history_points: int
    volatility_window_points: int = 24
    spread_decay_bps: float = 45.0
    join_best_queue_factor: float = 0.85
    off_best_queue_factor: float = 0.35
    synthetic_orderbook_half_spread_bps: float = 18.0
    synthetic_orderbook_depth_usd: float = 125.0
    telemetry_path: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Orderbook normalization
# ---------------------------------------------------------------------------

def normalize_orderbook_snapshots(
    raw_snapshots: Any,
    history: list[tuple[int, float]],
    params: PairReplayParams,
) -> tuple[dict[int, OrderBookSnapshot], str]:
    """Parse raw snapshots or synthesize from history."""
    snapshots: dict[int, OrderBookSnapshot] = {}
    if isinstance(raw_snapshots, list):
        for item in raw_snapshots:
            if not isinstance(item, dict):
                continue
            ts = _safe_int(item.get("t"), -1)
            if ts < 0:
                continue
            best_bid = _safe_float(item.get("best_bid"), -1.0)
            best_ask = _safe_float(item.get("best_ask"), -1.0)
            bid_size = _safe_float(item.get("bid_size_usd"), 0.0)
            ask_size = _safe_float(item.get("ask_size_usd"), 0.0)
            if best_bid < 0 or best_ask < 0 or best_bid > best_ask:
                continue
            snapshots[ts] = OrderBookSnapshot(
                t=ts,
                best_bid=best_bid,
                best_ask=best_ask,
                bid_size_usd=max(0.0, bid_size),
                ask_size_usd=max(0.0, ask_size),
            )
    if snapshots:
        return snapshots, "historical"

    # Synthesize from mid prices
    half_spread = params.synthetic_orderbook_half_spread_bps / 10000.0
    synthetic: dict[int, OrderBookSnapshot] = {}
    for ts, mid in history:
        synthetic[ts] = OrderBookSnapshot(
            t=ts,
            best_bid=clamp(mid - half_spread, 0.001, 0.999),
            best_ask=clamp(mid + half_spread, 0.001, 0.999),
            bid_size_usd=params.synthetic_orderbook_depth_usd,
            ask_size_usd=params.synthetic_orderbook_depth_usd,
        )
    return synthetic, "synthetic"


def write_telemetry_records(path: str, records: list[dict[str, Any]]) -> None:
    if not path or not records:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True))
            handle.write("\n")


# ---------------------------------------------------------------------------
# Equity + fill helpers
# ---------------------------------------------------------------------------

def _pair_equity(
    *,
    cash_usd: float,
    primary_shares: float,
    pair_shares: float,
    primary_price: float,
    pair_price: float,
    unwind_cost_bps: float,
) -> float:
    primary_value = primary_shares * primary_price
    pair_value = pair_shares * pair_price
    liquidation_cost = (abs(primary_value) + abs(pair_value)) * unwind_cost_bps / 10000.0
    return cash_usd + primary_value + pair_value - liquidation_cost


def _apply_fill(
    *,
    side: str,
    fill_notional: float,
    fill_price: float,
    cash_usd: float,
    position_shares: float,
) -> tuple[float, float]:
    shares = fill_notional / max(fill_price, 0.01)
    if side == "buy":
        cash_usd -= shares * fill_price
        position_shares += shares
    else:
        cash_usd += shares * fill_price
        position_shares -= shares
    return cash_usd, position_shares


def _fill_fraction(
    *,
    side: str,
    quote_price: float,
    quote_notional: float,
    current_book: OrderBookSnapshot,
    next_book: OrderBookSnapshot,
    next_mid: float,
    spread_bps: float,
    params: PairReplayParams,
) -> float:
    if quote_notional <= 0.0:
        return 0.0
    if side == "buy":
        touched_price = min(next_mid, next_book.best_bid)
        touched_distance_bps = max(0.0, (quote_price - touched_price) * 10000.0)
        displayed_size = next_book.ask_size_usd
        queue_factor = (
            params.join_best_queue_factor
            if quote_price >= current_book.best_bid
            else params.off_best_queue_factor
        )
    else:
        touched_price = max(next_mid, next_book.best_ask)
        touched_distance_bps = max(0.0, (touched_price - quote_price) * 10000.0)
        displayed_size = next_book.bid_size_usd
        queue_factor = (
            params.join_best_queue_factor
            if quote_price <= current_book.best_ask
            else params.off_best_queue_factor
        )
    if touched_distance_bps <= 0.0:
        return 0.0
    half_spread_bps = max(spread_bps / 2.0, 1.0)
    touch_ratio = clamp(touched_distance_bps / half_spread_bps, 0.0, 1.0)
    spread_decay = math.exp(-max(0.0, spread_bps) / max(params.spread_decay_bps, 1.0))
    depth_factor = clamp(math.sqrt(max(displayed_size, 0.0) / max(quote_notional, 1e-9)), 0.0, 1.0)
    return clamp(
        params.participation_rate * touch_ratio * spread_decay * queue_factor * depth_factor,
        0.0,
        1.0,
    )


# ---------------------------------------------------------------------------
# Main simulation
# ---------------------------------------------------------------------------

def simulate_pair_backtest(
    market: dict[str, Any],
    params: PairReplayParams,
    allocated_capital: float = 0.0,
) -> dict[str, Any]:
    """Run event-driven stateful pair replay with carried cash and inventory.

    Kalshi-specific notes:
    - Prices arrive in 0.01-0.99 decimal range (already converted from cents).
    - 1 contract = $1 notional at resolution. Shares map directly to contracts.
    - No maker rebate on Kalshi (we set rebate to 0).
    """
    primary_history: list[tuple[int, float]] = market["history"]
    pair_history: list[tuple[int, float]] = market["pair_history"]
    index_pair = {t: p for t, p in pair_history}
    primary_books = market.get("orderbooks", {})
    pair_books = market.get("pair_orderbooks", {})

    # Time-align the two series
    aligned_primary: list[tuple[int, float]] = []
    aligned_pair: list[tuple[int, float]] = []
    for t, primary_price in primary_history:
        pair_price = index_pair.get(t)
        if pair_price is None:
            continue
        aligned_primary.append((t, primary_price))
        aligned_pair.append((t, pair_price))

    capital = allocated_capital if allocated_capital > 0.0 else params.bankroll_usd
    window = max(3, params.volatility_window_points)
    if len(aligned_primary) < max(params.min_history_points, window + 2):
        return {
            "market_id": market.get("market_id", ""),
            "pair_market_id": market.get("pair_market_id", ""),
            "considered_points": 0,
            "quoted_points": 0,
            "skipped_points": 0,
            "fill_events": 0,
            "filled_notional_usd": 0.0,
            "pnl_usd": 0.0,
            "equity_curve": [capital],
            "telemetry": [],
            "event_pnls": [],
            "orderbook_mode": _safe_str(market.get("orderbook_mode"), "unknown"),
        }

    primary_position_shares = 0.0
    pair_position_shares = 0.0
    cash_usd = capital
    considered = 0
    quoted = 0
    skipped = 0
    fill_events = 0
    filled_notional = 0.0
    telemetry: list[dict[str, Any]] = []
    event_pnls: list[float] = []
    equity_curve = [capital]
    basis_series_bps = [
        (aligned_primary[idx][1] - aligned_pair[idx][1]) * 10000.0
        for idx in range(len(aligned_primary))
    ]
    end_ts = _safe_int(market.get("end_ts"), 0)

    for idx in range(window, len(aligned_primary) - 1):
        ts, primary_mid = aligned_primary[idx]
        _, pair_mid = aligned_pair[idx]
        next_ts, next_primary_mid = aligned_primary[idx + 1]
        _, next_pair_mid = aligned_pair[idx + 1]
        primary_book = primary_books.get(ts)
        next_primary_book = primary_books.get(next_ts, primary_book)
        pair_book = pair_books.get(ts)
        next_pair_book = pair_books.get(next_ts, pair_book)

        considered += 1
        record: dict[str, Any] = {
            "t": ts,
            "market_id": market.get("market_id", ""),
            "pair_market_id": market.get("pair_market_id", ""),
            "primary_mid_price": round(primary_mid, 6),
            "pair_mid_price": round(pair_mid, 6),
        }

        if primary_book is None or next_primary_book is None or pair_book is None or next_pair_book is None:
            skipped += 1
            record["status"] = "skipped"
            record["reason"] = "missing_orderbook_snapshot"
            telemetry.append(record)
            equity_curve.append(max(0.0, _pair_equity(
                cash_usd=cash_usd,
                primary_shares=primary_position_shares,
                pair_shares=pair_position_shares,
                primary_price=next_primary_mid,
                pair_price=next_pair_mid,
                unwind_cost_bps=params.expected_unwind_cost_bps,
            )))
            continue

        if not (0.01 < primary_mid < 0.99 and 0.01 < pair_mid < 0.99):
            skipped += 1
            record["status"] = "skipped"
            record["reason"] = "invalid_mid_prices"
            telemetry.append(record)
            equity_curve.append(max(0.0, _pair_equity(
                cash_usd=cash_usd,
                primary_shares=primary_position_shares,
                pair_shares=pair_position_shares,
                primary_price=next_primary_mid,
                pair_price=next_pair_mid,
                unwind_cost_bps=params.expected_unwind_cost_bps,
            )))
            continue

        basis_bps = basis_series_bps[idx]
        abs_basis_bps = abs(basis_bps)
        expected_convergence_bps = abs_basis_bps * params.expected_convergence_ratio
        edge_bps = expected_convergence_bps - params.expected_unwind_cost_bps
        basis_volatility_bps = pstdev(basis_series_bps[idx - window:idx]) if window > 1 else abs_basis_bps
        current_primary_notional = primary_position_shares * primary_mid
        current_pair_notional = pair_position_shares * pair_mid
        outstanding_notional = abs(current_primary_notional) + abs(current_pair_notional)

        desired_primary_notional = current_primary_notional
        desired_pair_notional = current_pair_notional
        reason = "hold_inventory"
        if abs_basis_bps >= params.basis_entry_bps and edge_bps >= params.min_edge_bps:
            target_pair_notional = params.base_pair_notional_usd * min(
                1.8,
                abs_basis_bps / max(params.basis_entry_bps, 1.0),
            )
            target_pair_notional = min(
                target_pair_notional,
                params.max_notional_per_pair_usd,
                params.max_leg_notional_usd,
            )
            if basis_bps > 0.0:
                desired_primary_notional = -target_pair_notional
                desired_pair_notional = target_pair_notional
            else:
                desired_primary_notional = target_pair_notional
                desired_pair_notional = -target_pair_notional
            reason = "basis_entry"
        elif abs(current_primary_notional) > 1e-9 or abs(current_pair_notional) > 1e-9:
            if abs_basis_bps <= params.basis_exit_bps or edge_bps < params.min_edge_bps:
                desired_primary_notional = 0.0
                desired_pair_notional = 0.0
                reason = "basis_exit"
        else:
            if abs_basis_bps < params.basis_entry_bps:
                reason = "basis_below_entry_threshold"
            elif edge_bps < params.min_edge_bps:
                reason = "negative_or_thin_edge"

        delta_primary_notional = desired_primary_notional - current_primary_notional
        delta_pair_notional = desired_pair_notional - current_pair_notional
        primary_side = "buy" if delta_primary_notional > 1e-9 else "sell" if delta_primary_notional < -1e-9 else ""
        pair_side = "buy" if delta_pair_notional > 1e-9 else "sell" if delta_pair_notional < -1e-9 else ""

        is_exit = desired_primary_notional == 0.0 and desired_pair_notional == 0.0
        quote_cap = params.base_pair_notional_usd * (1.25 if is_exit else min(1.8, abs_basis_bps / max(params.basis_entry_bps, 1.0)))
        primary_quote_notional = min(abs(delta_primary_notional), quote_cap) if primary_side else 0.0
        pair_quote_notional = min(abs(delta_pair_notional), quote_cap) if pair_side else 0.0

        # Enforce total notional cap
        increasing_primary = primary_quote_notional > 0.0 and abs(desired_primary_notional) > abs(current_primary_notional) + 1e-9
        increasing_pair = pair_quote_notional > 0.0 and abs(desired_pair_notional) > abs(current_pair_notional) + 1e-9
        growth_requested = 0.0
        if increasing_primary:
            growth_requested += primary_quote_notional
        if increasing_pair:
            growth_requested += pair_quote_notional
        remaining_total = max(0.0, params.max_total_notional_usd - outstanding_notional)
        if growth_requested > 0.0:
            if remaining_total <= 0.0:
                if increasing_primary:
                    primary_quote_notional = 0.0
                if increasing_pair:
                    pair_quote_notional = 0.0
            elif growth_requested > remaining_total:
                scale = remaining_total / growth_requested
                if increasing_primary:
                    primary_quote_notional *= scale
                if increasing_pair:
                    pair_quote_notional *= scale

        if primary_quote_notional <= 0.0 and pair_quote_notional <= 0.0:
            skipped += 1
            record.update({
                "status": "skipped",
                "reason": reason,
                "basis_bps": round(basis_bps, 6),
                "edge_bps": round(edge_bps, 6),
            })
            telemetry.append(record)
            equity_curve.append(max(0.0, _pair_equity(
                cash_usd=cash_usd,
                primary_shares=primary_position_shares,
                pair_shares=pair_position_shares,
                primary_price=next_primary_mid,
                pair_price=next_pair_mid,
                unwind_cost_bps=params.expected_unwind_cost_bps,
            )))
            continue

        quoted += 1
        primary_spread_bps = max((primary_book.best_ask - primary_book.best_bid) * 10000.0, 1.0)
        pair_spread_bps = max((pair_book.best_ask - pair_book.best_bid) * 10000.0, 1.0)
        primary_quote_price = primary_book.best_bid if primary_side == "buy" else primary_book.best_ask if primary_side == "sell" else 0.0
        pair_quote_price = pair_book.best_bid if pair_side == "buy" else pair_book.best_ask if pair_side == "sell" else 0.0
        equity_before = _pair_equity(
            cash_usd=cash_usd,
            primary_shares=primary_position_shares,
            pair_shares=pair_position_shares,
            primary_price=primary_mid,
            pair_price=pair_mid,
            unwind_cost_bps=params.expected_unwind_cost_bps,
        )

        primary_fill_fraction = 0.0
        pair_fill_fraction = 0.0
        primary_fill_notional = 0.0
        pair_fill_notional = 0.0

        if primary_side:
            primary_fill_fraction = _fill_fraction(
                side=primary_side,
                quote_price=primary_quote_price,
                quote_notional=primary_quote_notional,
                current_book=primary_book,
                next_book=next_primary_book,
                next_mid=next_primary_mid,
                spread_bps=primary_spread_bps,
                params=params,
            )
            primary_fill_notional = primary_quote_notional * primary_fill_fraction
        if pair_side:
            pair_fill_fraction = _fill_fraction(
                side=pair_side,
                quote_price=pair_quote_price,
                quote_notional=pair_quote_notional,
                current_book=pair_book,
                next_book=next_pair_book,
                next_mid=next_pair_mid,
                spread_bps=pair_spread_bps,
                params=params,
            )
            pair_fill_notional = pair_quote_notional * pair_fill_fraction

        if primary_fill_notional > 0.0:
            cash_usd, primary_position_shares = _apply_fill(
                side=primary_side,
                fill_notional=primary_fill_notional,
                fill_price=primary_quote_price,
                cash_usd=cash_usd,
                position_shares=primary_position_shares,
            )
            filled_notional += primary_fill_notional
            fill_events += 1
        if pair_fill_notional > 0.0:
            cash_usd, pair_position_shares = _apply_fill(
                side=pair_side,
                fill_notional=pair_fill_notional,
                fill_price=pair_quote_price,
                cash_usd=cash_usd,
                position_shares=pair_position_shares,
            )
            filled_notional += pair_fill_notional
            fill_events += 1

        equity_after = max(0.0, _pair_equity(
            cash_usd=cash_usd,
            primary_shares=primary_position_shares,
            pair_shares=pair_position_shares,
            primary_price=next_primary_mid,
            pair_price=next_pair_mid,
            unwind_cost_bps=params.expected_unwind_cost_bps,
        ))
        equity_curve.append(equity_after)
        if (
            primary_fill_notional > 0.0
            or pair_fill_notional > 0.0
            or abs(primary_position_shares) > 1e-9
            or abs(pair_position_shares) > 1e-9
        ):
            event_pnls.append(equity_after - equity_before)

        record.update({
            "status": "quoted",
            "reason": reason,
            "basis_bps": round(basis_bps, 6),
            "basis_volatility_bps": round(basis_volatility_bps, 6),
            "edge_bps": round(edge_bps, 6),
            "primary_fill_notional_usd": round(primary_fill_notional, 6),
            "pair_fill_notional_usd": round(pair_fill_notional, 6),
            "equity_after_usd": round(equity_after, 6),
        })
        telemetry.append(record)
        if equity_after <= 0.0:
            break

    ending_equity = max(0.0, _pair_equity(
        cash_usd=cash_usd,
        primary_shares=primary_position_shares,
        pair_shares=pair_position_shares,
        primary_price=aligned_primary[-1][1] if aligned_primary else 0.5,
        pair_price=aligned_pair[-1][1] if aligned_pair else 0.5,
        unwind_cost_bps=params.expected_unwind_cost_bps,
    ))
    if not equity_curve or ending_equity != equity_curve[-1]:
        equity_curve.append(ending_equity)

    return {
        "market_id": market.get("market_id", ""),
        "pair_market_id": market.get("pair_market_id", ""),
        "considered_points": considered,
        "quoted_points": quoted,
        "skipped_points": skipped,
        "fill_events": fill_events,
        "filled_notional_usd": round(filled_notional, 4),
        "pnl_usd": round(ending_equity - capital, 6),
        "equity_curve": equity_curve,
        "telemetry": telemetry,
        "event_pnls": event_pnls,
        "orderbook_mode": _safe_str(market.get("orderbook_mode"), "unknown"),
    }
