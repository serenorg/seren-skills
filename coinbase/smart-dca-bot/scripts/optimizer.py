#!/usr/bin/env python3
"""Execution timing optimizer for DCA windows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


SUPPORTED_STRATEGIES = {
    "vwap_optimized",
    "momentum_dip",
    "spread_optimized",
    "time_weighted",
    "simple",
}


@dataclass
class ExecutionDecision:
    strategy: str
    should_execute: bool
    confidence_pct: float
    reason: str
    order_type: str
    limit_price: float | None
    slices: list[float] = field(default_factory=list)


def compute_rsi(prices: list[float], period: int = 14) -> float:
    if len(prices) < period + 1:
        return 50.0

    gains: list[float] = []
    losses: list[float] = []
    recent = prices[-(period + 1) :]
    for prev, curr in zip(recent, recent[1:]):
        delta = curr - prev
        if delta >= 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(delta))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _spread_bps(bid: float, ask: float) -> float:
    mid = (bid + ask) / 2.0 if bid and ask else 0.0
    if mid <= 0:
        return 9999.0
    return ((ask - bid) / mid) * 10000.0


def decide_execution(
    *,
    strategy: str,
    snapshot: dict[str, Any],
    window_progress: float,
    force_fill: bool,
) -> ExecutionDecision:
    """Return an execution decision for one strategy within the active DCA window."""
    if strategy not in SUPPORTED_STRATEGIES:
        raise ValueError(f"Unsupported execution strategy '{strategy}'")

    price = float(snapshot["price"])
    vwap = float(snapshot.get("vwap", price))
    bid = float(snapshot.get("bid", price))
    ask = float(snapshot.get("ask", price))
    low_24h = float(snapshot.get("low_24h", price))
    depth_score = float(snapshot.get("depth_score", 0.5))
    candles = [float(x) for x in snapshot.get("candles", [])]

    if force_fill or window_progress >= 0.99:
        return ExecutionDecision(
            strategy=strategy,
            should_execute=True,
            confidence_pct=100.0,
            reason="forced_fill_at_window_end",
            order_type="market",
            limit_price=None,
        )

    if strategy == "simple":
        return ExecutionDecision(
            strategy=strategy,
            should_execute=True,
            confidence_pct=100.0,
            reason="simple_executes_immediately",
            order_type="market",
            limit_price=None,
        )

    if strategy == "vwap_optimized":
        discount_bps = ((vwap - price) / vwap) * 10000 if vwap > 0 else 0
        threshold_bps = 6.0
        should_execute = discount_bps >= threshold_bps or window_progress > 0.85
        confidence = min(max((discount_bps + 20.0) * 2.0, 20.0), 95.0)
        limit_price = round(min(price, vwap * 0.999), 6)
        return ExecutionDecision(
            strategy=strategy,
            should_execute=should_execute,
            confidence_pct=round(confidence, 2),
            reason=f"discount_vs_vwap_bps={discount_bps:.2f}",
            order_type="limit",
            limit_price=limit_price,
        )

    if strategy == "momentum_dip":
        rsi_15m = compute_rsi(candles, period=14)
        dip = price <= low_24h * 1.02
        should_execute = (rsi_15m < 30.0 and dip) or window_progress > 0.90
        confidence = 90.0 - abs(30.0 - rsi_15m)
        return ExecutionDecision(
            strategy=strategy,
            should_execute=should_execute,
            confidence_pct=round(max(min(confidence, 92.0), 25.0), 2),
            reason=f"rsi_15m={rsi_15m:.2f} near_day_low={dip}",
            order_type="limit",
            limit_price=round(min(price, ask * 0.999), 6),
        )

    if strategy == "spread_optimized":
        spread = _spread_bps(bid, ask)
        should_execute = (spread <= 12.0 and depth_score >= 0.6) or window_progress > 0.92
        confidence = 95.0 - spread
        return ExecutionDecision(
            strategy=strategy,
            should_execute=should_execute,
            confidence_pct=round(max(min(confidence, 96.0), 20.0), 2),
            reason=f"spread_bps={spread:.2f} depth_score={depth_score:.2f}",
            order_type="limit",
            limit_price=round(bid + (ask - bid) * 0.35, 6),
        )

    # time_weighted
    slices = [0.25, 0.25, 0.25, 0.25]
    tranche_index = min(int(window_progress * len(slices)), len(slices) - 1)
    should_execute = True
    confidence = 80.0
    return ExecutionDecision(
        strategy=strategy,
        should_execute=should_execute,
        confidence_pct=confidence,
        reason=f"time_weighted_tranche={tranche_index + 1}/{len(slices)}",
        order_type="limit",
        limit_price=round(min(price, vwap), 6),
        slices=slices,
    )
