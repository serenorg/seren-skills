"""
Kelly Criterion - Optimal position sizing for Kalshi prediction markets

Uses the Kelly Criterion formula to calculate optimal bet sizes based on:
- Fair value probability estimate
- Market price (converted from cents to probability)
- Bankroll
- Risk parameters

Kalshi prices are in CENTS (1-99). Convert to probability (0.01-0.99) for calculations.
Contracts pay $1.00 if correct, $0.00 if wrong.
"""


def calculate_kelly_fraction(fair_value: float, market_price: float) -> float:
    """
    Calculate Kelly Criterion fraction

    Formula: kelly = (p * (b + 1) - 1) / b
    where:
        p = probability of winning (fair_value for BUY YES, 1-fair_value for BUY NO)
        b = odds (payout ratio)

    For prediction markets with prices 0-1:
    - BUY YES at price p: kelly = (fair_value - price) / (1 - price)
    - BUY NO at price p: kelly = (price - fair_value) / price

    Args:
        fair_value: Estimated true probability (0.0-1.0)
        market_price: Current market probability (0.0-1.0)

    Returns:
        Kelly fraction (can be negative if bet is -EV)
    """
    if fair_value > market_price:
        # BUY YES: we think true prob is higher than market
        if market_price >= 1.0:
            return 0.0
        kelly = (fair_value - market_price) / (1 - market_price)
    elif fair_value < market_price:
        # BUY NO: we think true prob is lower than market
        if market_price <= 0.0:
            return 0.0
        kelly = (market_price - fair_value) / market_price
    else:
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
        bankroll: Total bankroll in USD
        max_kelly_fraction: Maximum % of bankroll per trade (default 6%)
        kelly_fraction_divisor: Divide Kelly by this (default 4 for quarter-Kelly)

    Returns:
        (position_size_usd, side) where side is 'yes' or 'no'
    """
    kelly = calculate_kelly_fraction(fair_value, market_price)

    if fair_value > market_price:
        side = 'yes'
    elif fair_value < market_price:
        side = 'no'
    else:
        return 0.0, 'none'

    # Use fractional Kelly for conservatism
    kelly_adjusted = abs(kelly) / kelly_fraction_divisor

    # Cap at max fraction
    kelly_capped = min(kelly_adjusted, max_kelly_fraction)

    # Calculate position size in USD
    position_size = bankroll * kelly_capped

    # Minimum position: 1 contract at cheapest price ($0.01)
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

    Kalshi contracts pay $1.00 if correct, $0.00 if wrong.
    Cost per contract = price in cents / 100.

    Args:
        fair_value: Estimated true probability (0.0-1.0)
        market_price: Current market price (0.0-1.0)
        position_size: Position size in USD
        side: 'yes' or 'no'

    Returns:
        Expected value in USD
    """
    if side == 'yes':
        # Buy YES at market_price, pays $1 if yes
        cost_per_contract = market_price
        if cost_per_contract <= 0:
            return 0.0
        num_contracts = position_size / cost_per_contract
        # EV = P(yes) * $1 * contracts - cost
        ev = (fair_value * num_contracts) - position_size
    elif side == 'no':
        # Buy NO at (1 - market_price), pays $1 if no
        cost_per_contract = 1.0 - market_price
        if cost_per_contract <= 0:
            return 0.0
        num_contracts = position_size / cost_per_contract
        # EV = P(no) * $1 * contracts - cost
        ev = ((1.0 - fair_value) * num_contracts) - position_size
    else:
        ev = 0.0

    return round(ev, 2)


def calculate_annualized_return(edge: float, days_to_resolution: float) -> float:
    """
    Calculate annualized return from edge and time to resolution.

    Args:
        edge: Absolute edge (e.g. 0.13 for 13%)
        days_to_resolution: Days until market resolves

    Returns:
        Annualized return (e.g. 0.52 for 52%/year)
    """
    if days_to_resolution <= 0:
        return float('inf')
    years = days_to_resolution / 365.0
    return edge / years


def cents_to_probability(cents: int) -> float:
    """Convert Kalshi price in cents (1-99) to probability (0.01-0.99)"""
    return cents / 100.0


def probability_to_cents(prob: float) -> int:
    """Convert probability (0.01-0.99) to Kalshi price in cents (1-99)"""
    return max(1, min(99, round(prob * 100)))


# Example usage and tests
if __name__ == '__main__':
    # Test case 1: Underpriced YES (BUY YES)
    fair = 0.67
    price = 0.54
    bankroll = 100.0

    k = calculate_kelly_fraction(fair, price)
    size, side = calculate_position_size(fair, price, bankroll)
    edge = calculate_edge(fair, price)
    ev = calculate_expected_value(fair, price, size, side)
    ann = calculate_annualized_return(edge, 30)

    print("Test Case 1: Underpriced YES Market")
    print(f"Fair Value: {fair * 100:.1f}%")
    print(f"Market Price: {price * 100:.1f}% ({probability_to_cents(price)} cents)")
    print(f"Kelly Fraction: {k * 100:.2f}%")
    print(f"Position Size: ${size:.2f}")
    print(f"Side: {side}")
    print(f"Edge: {edge * 100:.1f}%")
    print(f"Expected Value: ${ev:.2f}")
    print(f"Annualized Return: {ann * 100:.1f}%")
    print()

    # Test case 2: Overpriced YES (BUY NO)
    fair = 0.28
    price = 0.40

    k = calculate_kelly_fraction(fair, price)
    size, side = calculate_position_size(fair, price, bankroll)
    edge = calculate_edge(fair, price)
    ev = calculate_expected_value(fair, price, size, side)

    print("Test Case 2: Overpriced YES Market (buy NO)")
    print(f"Fair Value: {fair * 100:.1f}%")
    print(f"Market Price: {price * 100:.1f}% ({probability_to_cents(price)} cents)")
    print(f"Kelly Fraction: {k * 100:.2f}%")
    print(f"Position Size: ${size:.2f}")
    print(f"Side: {side}")
    print(f"Edge: {edge * 100:.1f}%")
    print(f"Expected Value: ${ev:.2f}")
