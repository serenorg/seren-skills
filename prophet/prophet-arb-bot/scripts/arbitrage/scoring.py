"""Spread scoring + position sizing for the Prophet ↔ Polymarket pair.

For each pair (prophet_market_id, polymarket_condition_id) we compute:

    spread_yes = prophet_yes_price - polymarket_yes_price

Sign convention:
  - spread_yes > 0  → prophet's YES is overpriced vs polymarket → SELL YES on prophet
  - spread_yes < 0  → prophet's YES is underpriced vs polymarket → BUY YES on prophet

We only emit an opportunity if `|spread_yes| >= min_spread`. The default
0.03 (3 cents on a 0-1 probability) leaves room for fee + slippage; the
operator can tune via config.

Sizing uses fractional Kelly (default 0.25) with explicit per-trade
caps. Kelly works for binary markets because the payout is symmetric
and known: at price p, a $1 buy of YES pays $1 if YES resolves true and
$0 otherwise. Edge = (fair − p) / (1 − p) for buys, (p − fair) / p for
sells, where `fair` is the polymarket-implied fair value.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PairPrices:
    """Aligned price snapshot for one prophet+polymarket pair."""

    prophet_market_id: str
    polymarket_condition_id: str
    prophet_yes: float
    prophet_no: float
    polymarket_yes: float
    polymarket_no: float

    def is_priced(self) -> bool:
        return self.prophet_yes > 0 and self.polymarket_yes > 0


@dataclass
class ScoringConfig:
    min_spread: float = 0.03
    max_spread: float = 0.30  # >30c = market broken or stale, fail closed
    kelly_fraction: float = 0.25
    max_trade_size_usdc: float = 50.0
    min_trade_size_usdc: float = 5.0
    bankroll_usdc: float = 200.0


@dataclass
class Opportunity:
    """A scored arb opportunity. Caller decides whether to execute."""

    prophet_market_id: str
    polymarket_condition_id: str
    side: str  # "buy" | "sell"
    outcome: str  # "yes" | "no"
    prophet_price: float
    fair_price: float  # polymarket reference
    spread: float  # prophet − polymarket, signed for the YES leg
    edge: float  # expected return per dollar on this leg
    size_usdc: float  # recommended trade size (already capped)
    limit_price: float  # the price we'll quote on prophet
    reason: str = ""
    health_warnings: list[str] = field(default_factory=list)

    def is_actionable(self) -> bool:
        """Whether the agent should submit this opportunity as an order.

        The threshold decision is owned by ``score_pair`` — sizes below
        ``config.min_trade_size_usdc`` are already zeroed there. So this
        helper only needs to confirm that the score emitted a positive
        size and no health warnings tripped a panic exit.

        Historic bug (fixed): this used to hard-code ``size_usdc >= 1.0``,
        which silently blocked legitimate trades whenever the operator
        configured ``min_trade_size_usdc`` below $1.
        """
        return self.size_usdc > 0 and not self.health_warnings


def score_pair(
    prices: PairPrices,
    *,
    config: ScoringConfig,
    health_warnings: list[str] | None = None,
) -> Opportunity | None:
    """Return a scored opportunity, or None if no edge exists.

    The function is total — no exceptions on missing prices. Callers
    that want strict semantics should validate `prices.is_priced()`
    first and raise `InsufficientPriceDataError` themselves.
    """
    if not prices.is_priced():
        return None

    spread = prices.prophet_yes - prices.polymarket_yes
    abs_spread = abs(spread)

    # Below the actionable threshold — not worth fees/slippage.
    if abs_spread < config.min_spread:
        return None

    # Above the panic threshold — almost certainly stale or broken.
    if abs_spread > config.max_spread:
        warnings = list(health_warnings or [])
        warnings.append(
            f"spread {abs_spread:.3f} exceeds max_spread {config.max_spread:.3f}; "
            "likely stale prophet odds or a market mismatch"
        )
        return Opportunity(
            prophet_market_id=prices.prophet_market_id,
            polymarket_condition_id=prices.polymarket_condition_id,
            side="buy",
            outcome="yes",
            prophet_price=prices.prophet_yes,
            fair_price=prices.polymarket_yes,
            spread=spread,
            edge=0.0,
            size_usdc=0.0,
            limit_price=prices.prophet_yes,
            reason="blocked_max_spread_exceeded",
            health_warnings=warnings,
        )

    # Determine side + outcome. We always trade the YES leg; selling NO
    # is equivalent under binary symmetry but adds CLOB-side complexity.
    if spread > 0:
        # Prophet YES overpriced → sell YES on prophet at limit_price.
        side = "sell"
        prophet_price = prices.prophet_yes
        fair_price = prices.polymarket_yes
        # Edge per dollar of notional sold: how much above fair is prophet?
        edge = (prophet_price - fair_price) / prophet_price
        # Quote one notch toward fair to encourage fill while preserving edge.
        limit_price = max(
            fair_price + config.min_spread * 0.5, fair_price + 0.005
        )
    else:
        # Prophet YES underpriced → buy YES on prophet at limit_price.
        side = "buy"
        prophet_price = prices.prophet_yes
        fair_price = prices.polymarket_yes
        edge = (fair_price - prophet_price) / (1.0 - prophet_price)
        limit_price = min(
            fair_price - config.min_spread * 0.5, fair_price - 0.005
        )

    # Snap limit price into Prophet's binary (0, 1) bound. Limits at the
    # extremes will be rejected by the order endpoint.
    limit_price = max(0.01, min(0.99, limit_price))

    # Fractional Kelly sizing.
    kelly_fraction = max(0.0, edge) * config.kelly_fraction
    raw_size = config.bankroll_usdc * kelly_fraction
    sized = max(0.0, min(raw_size, config.max_trade_size_usdc))
    if sized < config.min_trade_size_usdc:
        sized = 0.0  # too small to bother — skip

    return Opportunity(
        prophet_market_id=prices.prophet_market_id,
        polymarket_condition_id=prices.polymarket_condition_id,
        side=side,
        outcome="yes",
        prophet_price=prophet_price,
        fair_price=fair_price,
        spread=spread,
        edge=edge,
        size_usdc=sized,
        limit_price=limit_price,
        reason=("ok" if sized > 0 else "blocked_below_min_trade_size"),
        health_warnings=list(health_warnings or []),
    )


def score_batch(
    pairs: list[PairPrices],
    *,
    config: ScoringConfig,
    health_warnings_by_pair: dict[str, list[str]] | None = None,
) -> list[Opportunity]:
    """Convenience wrapper — score every pair, drop Nones."""
    health_warnings_by_pair = health_warnings_by_pair or {}
    out: list[Opportunity] = []
    for prices in pairs:
        warnings = health_warnings_by_pair.get(prices.prophet_market_id, [])
        opp = score_pair(prices, config=config, health_warnings=warnings)
        if opp is not None:
            out.append(opp)
    return out
