"""
Kelly Criterion - Optimal position sizing for prediction markets

Uses the Kelly Criterion formula to calculate optimal bet sizes based on:
- Fair value probability estimate
- Market price
- Bankroll
- Risk parameters
"""


def calculate_kelly_fraction(fair_value: float, market_price: float) -> float:
    """
    Calculate Kelly Criterion fraction

    Formula: kelly = (p * (b + 1) - 1) / b
    where:
        p = probability of winning (fair_value for BUY, 1-fair_value for SELL)
        b = odds (payout ratio)

    For prediction markets with prices 0-1:
    - BUY at price p: kelly = (fair_value - price) / (1 - price)
    - SELL at price p: kelly = (price - fair_value) / price

    Args:
        fair_value: Estimated true probability (0.0-1.0)
        market_price: Current market probability (0.0-1.0)

    Returns:
        Kelly fraction (can be negative if bet is -EV)
    """
    # Determine if we should BUY or SELL
    if fair_value > market_price:
        # BUY: we think true prob is higher than market
        kelly = (fair_value - market_price) / (1 - market_price)
    elif fair_value < market_price:
        # SELL: we think true prob is lower than market
        kelly = (market_price - fair_value) / market_price
    else:
        # No edge
        kelly = 0.0

    return kelly


def calculate_position_size(
    fair_value: float,
    market_price: float,
    bankroll: float,
    max_kelly_fraction: float = 0.06,
    kelly_fraction_divisor: int = 4
) -> tuple[float, str]:
    """
    Calculate optimal position size using Kelly Criterion

    Uses quarter-Kelly by default (divide by 4) for conservatism.
    Further capped by max_kelly_fraction of bankroll.

    Args:
        fair_value: Estimated true probability (0.0-1.0)
        market_price: Current market price (0.0-1.0)
        bankroll: Total bankroll in USDC
        max_kelly_fraction: Maximum % of bankroll per trade (default 6%)
        kelly_fraction_divisor: Divide Kelly by this (default 4 for quarter-Kelly)

    Returns:
        (position_size, side) where side is 'BUY' or 'SELL'
    """
    # Calculate raw Kelly fraction
    kelly = calculate_kelly_fraction(fair_value, market_price)

    # Determine side
    if fair_value > market_price:
        side = 'BUY'
    elif fair_value < market_price:
        side = 'SELL'
    else:
        return 0.0, 'NONE'

    # Use fractional Kelly for conservatism
    kelly_adjusted = abs(kelly) / kelly_fraction_divisor

    # Cap at max fraction
    kelly_capped = min(kelly_adjusted, max_kelly_fraction)

    # Calculate position size
    position_size = bankroll * kelly_capped

    # Minimum position size $0.10 (Polymarket minimum)
    if position_size < 0.10:
        position_size = 0.0

    return round(position_size, 2), side


def calculate_edge(fair_value: float, market_price: float) -> float:
    """
    Calculate expected edge (percentage mispricing)

    Args:
        fair_value: Estimated true probability (0.0-1.0)
        market_price: Current market price (0.0-1.0)

    Returns:
        Edge as percentage (e.g., 0.13 for 13% edge)
    """
    return abs(fair_value - market_price)


def calculate_expected_value(
    fair_value: float,
    market_price: float,
    position_size: float,
    side: str
) -> float:
    """
    Calculate expected value of a trade

    Args:
        fair_value: Estimated true probability (0.0-1.0)
        market_price: Current market price (0.0-1.0)
        position_size: Position size in USDC
        side: 'BUY' or 'SELL'

    Returns:
        Expected value in USDC
    """
    if side == 'BUY':
        # EV = (fair_value * max_win) - ((1 - fair_value) * position_size)
        max_win = position_size / market_price  # What we get if we win
        ev = (fair_value * max_win) - ((1 - fair_value) * position_size)
    elif side == 'SELL':
        # EV = ((1 - fair_value) * max_win) - (fair_value * position_size)
        max_win = position_size / (1 - market_price)
        ev = ((1 - fair_value) * max_win) - (fair_value * position_size)
    else:
        ev = 0.0

    return round(ev, 2)


# Example usage and tests
if __name__ == '__main__':
    # Test case 1: Underpriced market (BUY)
    fair = 0.67
    price = 0.54
    bankroll = 100.0

    kelly = calculate_kelly_fraction(fair, price)
    size, side = calculate_position_size(fair, price, bankroll)
    edge = calculate_edge(fair, price)
    ev = calculate_expected_value(fair, price, size, side)

    print("Test Case 1: Underpriced Market")
    print(f"Fair Value: {fair * 100:.1f}%")
    print(f"Market Price: {price * 100:.1f}%")
    print(f"Kelly Fraction: {kelly * 100:.2f}%")
    print(f"Position Size: ${size:.2f}")
    print(f"Side: {side}")
    print(f"Edge: {edge * 100:.1f}%")
    print(f"Expected Value: ${ev:.2f}")
    print()

    # Test case 2: Overpriced market (SELL)
    fair = 0.28
    price = 0.32

    kelly = calculate_kelly_fraction(fair, price)
    size, side = calculate_position_size(fair, price, bankroll)
    edge = calculate_edge(fair, price)
    ev = calculate_expected_value(fair, price, size, side)

    print("Test Case 2: Overpriced Market")
    print(f"Fair Value: {fair * 100:.1f}%")
    print(f"Market Price: {price * 100:.1f}%")
    print(f"Kelly Fraction: {kelly * 100:.2f}%")
    print(f"Position Size: ${size:.2f}")
    print(f"Side: {side}")
    print(f"Edge: {edge * 100:.1f}%")
    print(f"Expected Value: ${ev:.2f}")
