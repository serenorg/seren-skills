"""Critical-only tests for arbitrage.scoring.

Critical-only doctrine (mirrors prophet-bounty-runner): each test
exists because of a specific defect or invariant; tests that exercise
trivial getters or duplicate other coverage are intentionally omitted.

Coverage:
  - test_score_pair_no_edge_returns_none: enforces the min_spread floor
    so we don't trade pure noise.
  - test_score_pair_panic_threshold_blocks_trade: enforces the
    max_spread ceiling so we don't trade against a stale or broken price.
  - test_score_pair_buy_side_for_underpriced_prophet: locks the sign
    convention; flipping this would cause buys to be queued as sells.
  - test_score_pair_sell_side_for_overpriced_prophet: same convention,
    other side.
  - test_score_pair_kelly_bounds: ensures the size cap is respected so
    a high-edge pair can't deplete the bankroll in one trade.
  - test_score_pair_below_min_trade_size_returns_zero_size: invariant
    that protects fee-burn-on-tiny-size scenarios.
"""

from __future__ import annotations

from arbitrage.scoring import (
    Opportunity,
    PairPrices,
    ScoringConfig,
    score_pair,
)


def _config(**overrides) -> ScoringConfig:
    base = ScoringConfig()
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_score_pair_no_edge_returns_none() -> None:
    prices = PairPrices(
        prophet_market_id="m1",
        polymarket_condition_id="c1",
        prophet_yes=0.50,
        prophet_no=0.50,
        polymarket_yes=0.51,  # 1¢ spread, below default 3¢ floor
        polymarket_no=0.49,
    )
    assert score_pair(prices, config=_config()) is None


def test_score_pair_panic_threshold_blocks_trade() -> None:
    prices = PairPrices(
        prophet_market_id="m1",
        polymarket_condition_id="c1",
        prophet_yes=0.10,
        prophet_no=0.90,
        polymarket_yes=0.50,  # 40¢ spread — likely stale
        polymarket_no=0.50,
    )
    opp = score_pair(prices, config=_config())
    assert isinstance(opp, Opportunity)
    assert opp.size_usdc == 0.0
    assert opp.reason == "blocked_max_spread_exceeded"
    assert any("max_spread" in w for w in opp.health_warnings)


def test_score_pair_buy_side_for_underpriced_prophet() -> None:
    prices = PairPrices(
        prophet_market_id="m1",
        polymarket_condition_id="c1",
        prophet_yes=0.40,
        prophet_no=0.60,
        polymarket_yes=0.50,
        polymarket_no=0.50,
    )
    opp = score_pair(prices, config=_config())
    assert opp is not None
    assert opp.side == "buy"
    assert opp.outcome == "yes"
    assert opp.spread < 0
    assert opp.limit_price < prices.polymarket_yes


def test_score_pair_sell_side_for_overpriced_prophet() -> None:
    prices = PairPrices(
        prophet_market_id="m1",
        polymarket_condition_id="c1",
        prophet_yes=0.60,
        prophet_no=0.40,
        polymarket_yes=0.50,
        polymarket_no=0.50,
    )
    opp = score_pair(prices, config=_config())
    assert opp is not None
    assert opp.side == "sell"
    assert opp.outcome == "yes"
    assert opp.spread > 0
    assert opp.limit_price > prices.polymarket_yes


def test_score_pair_kelly_bounds() -> None:
    # Massive (but in-band) edge — kelly_fraction*edge*bankroll would
    # blow past max_trade_size_usdc, so the cap must clip.
    config = _config(
        bankroll_usdc=10_000.0,
        max_trade_size_usdc=50.0,
        min_trade_size_usdc=5.0,
    )
    prices = PairPrices(
        prophet_market_id="m1",
        polymarket_condition_id="c1",
        prophet_yes=0.30,  # 20¢ underpriced — well under max_spread 30¢
        prophet_no=0.70,
        polymarket_yes=0.50,
        polymarket_no=0.50,
    )
    opp = score_pair(prices, config=config)
    assert opp is not None
    assert 0 < opp.size_usdc <= 50.0


def test_score_pair_below_min_trade_size_returns_zero_size() -> None:
    config = _config(
        bankroll_usdc=10.0,
        kelly_fraction=0.01,  # tiny → sized < min_trade_size
        min_trade_size_usdc=5.0,
        max_trade_size_usdc=50.0,
    )
    prices = PairPrices(
        prophet_market_id="m1",
        polymarket_condition_id="c1",
        prophet_yes=0.45,
        prophet_no=0.55,
        polymarket_yes=0.50,
        polymarket_no=0.50,
    )
    opp = score_pair(prices, config=config)
    assert opp is not None
    assert opp.size_usdc == 0.0
    assert opp.reason == "blocked_below_min_trade_size"
