"""Critical-only tests for arbitrage.scoring.

Critical-only doctrine (mirrors prophet-bounty-runner): each test
exists because of a specific defect or invariant; tests that exercise
trivial getters or duplicate other coverage are intentionally omitted.

The emergency-exit safety posture (cancel_all of open orders on
unwind/flatten/close all, no marketable taker sells) relies on
``Opportunity.is_actionable`` correctly returning False whenever the
score is blocked or sized to zero — that contract is what stops a
runaway agent from quoting into a panic-tripped opportunity.

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
  - test_is_actionable_respects_config_min_trade_size_not_hardcoded_floor:
    regression for the $1.00 hardcoded floor that ignored the operator's
    config and silently blocked legitimate small trades.
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
    # Zero-size + blocked reason flow through is_actionable → False, so
    # the agent never reaches placeOrder. This is the same gate the
    # emergency-exit path (cancel_all of open orders) relies on.
    assert not opp.is_actionable()


def test_is_actionable_respects_config_min_trade_size_not_hardcoded_floor() -> None:
    """Regression for the $1.00 hardcoded floor in is_actionable.

    The bug: `Opportunity.is_actionable` compared ``size_usdc >= 1.0``
    instead of trusting the score_pair gate (which already zeros sizes
    below ``config.min_trade_size_usdc``). When an operator set
    ``min_trade_size_usdc=0.50``, a Kelly-sized $0.80 trade landed in
    the envelope with ``size_usdc=0.80`` but ``is_actionable=False``,
    silently blocking a legitimate small trade.

    Fix: ``is_actionable`` now returns True whenever score_pair emits a
    positive size and no health warnings — the threshold decision is
    owned by score_pair alone.
    """
    config = _config(
        bankroll_usdc=7.5,
        kelly_fraction=1.0,
        min_trade_size_usdc=0.50,
        max_trade_size_usdc=2.0,
    )
    prices = PairPrices(
        prophet_market_id="m1",
        polymarket_condition_id="c1",
        prophet_yes=0.56,  # 6¢ overpriced vs polymarket → edge ~10.7%
        prophet_no=0.44,
        polymarket_yes=0.50,
        polymarket_no=0.50,
    )
    opp = score_pair(prices, config=config)
    assert opp is not None
    assert opp.reason == "ok"
    # The kelly-sized trade lands between the config floor ($0.50) and
    # the old hardcoded floor ($1.00) — the bug would have blocked this.
    assert config.min_trade_size_usdc <= opp.size_usdc < 1.0
    assert opp.is_actionable()
