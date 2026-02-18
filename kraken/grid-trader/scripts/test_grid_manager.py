"""
Unit tests for GridManager.calculate_expected_profit.

Verifies:
- Gross profit is based on order size and quantity, not raw price spacing
- Fees cover both buy and sell legs
- Net profit < gross profit
- Return % is within realistic bounds for the given config
- Bankroll parameter used for return % when provided
"""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from grid_manager import GridManager


def _grid(
    min_price=45_000,
    max_price=55_000,
    grid_levels=20,
    spacing_percent=2.0,
    order_size_usd=50.0
) -> GridManager:
    return GridManager(
        min_price=min_price,
        max_price=max_price,
        grid_levels=grid_levels,
        spacing_percent=spacing_percent,
        order_size_usd=order_size_usd
    )


class TestCalculateExpectedProfit:
    def test_gross_profit_based_on_order_size_not_spacing(self):
        grid = _grid(order_size_usd=50.0)
        result = grid.calculate_expected_profit(fills_per_day=15)
        # avg_spacing = 10000 / 19 = 526.32; avg_price = 50000
        # buy_qty = 50 / 50000 = 0.001
        # gross_profit = 0.001 * 526.32 = 0.5263
        assert result['gross_profit_per_cycle'] < 1.0, (
            f"Gross profit {result['gross_profit_per_cycle']} should be < $1 "
            f"for a $50 order, not equal to the $526 price spacing"
        )

    def test_gross_profit_scales_with_order_size(self):
        small = _grid(order_size_usd=50.0).calculate_expected_profit()
        large = _grid(order_size_usd=500.0).calculate_expected_profit()
        assert abs(large['gross_profit_per_cycle'] / small['gross_profit_per_cycle'] - 10) < 0.01

    def test_fees_cover_both_legs(self):
        grid = _grid(order_size_usd=50.0)
        result = grid.calculate_expected_profit()
        # Buy notional = $50; sell notional ≈ $50.53; total fee base ≈ $100.53
        # fees = $100.53 * 0.0016 ≈ $0.1608
        assert result['fees_per_cycle'] > 0.15, "Fees should cover both buy and sell legs"
        assert result['fees_per_cycle'] < 0.20

    def test_net_profit_less_than_gross(self):
        result = _grid().calculate_expected_profit()
        assert result['net_profit_per_cycle'] < result['gross_profit_per_cycle']

    def test_net_profit_positive(self):
        # A viable grid should produce positive net profit per cycle
        result = _grid(order_size_usd=50.0).calculate_expected_profit()
        assert result['net_profit_per_cycle'] > 0

    def test_daily_return_with_bankroll_is_realistic(self):
        # With $1000 bankroll, daily return should be < 5% (not hundreds of percent)
        result = _grid(order_size_usd=50.0).calculate_expected_profit(
            fills_per_day=15, bankroll=1000.0
        )
        assert result['daily_return_percent'] < 5.0, (
            f"Daily return {result['daily_return_percent']}% is unrealistically high"
        )
        assert result['daily_return_percent'] > 0.0

    def test_monthly_return_with_bankroll_is_realistic(self):
        result = _grid(order_size_usd=50.0).calculate_expected_profit(
            fills_per_day=15, bankroll=1000.0
        )
        assert result['monthly_return_percent'] < 100.0
        assert result['monthly_return_percent'] > 0.0

    def test_bankroll_param_changes_return_percent(self):
        grid = _grid(order_size_usd=50.0)
        # deployed capital = order_size_usd * grid_levels = 50 * 20 = 1000
        # Use a bankroll different from deployed capital to see the difference
        with_bankroll = grid.calculate_expected_profit(fills_per_day=15, bankroll=2000.0)
        without = grid.calculate_expected_profit(fills_per_day=15, bankroll=1000.0)
        # $2000 bankroll → half the return% of $1000 bankroll
        assert abs(with_bankroll['daily_return_percent'] - without['daily_return_percent'] / 2) < 0.001

    def test_avg_spacing_usd_is_correct(self):
        grid = _grid(min_price=45_000, max_price=55_000, grid_levels=20)
        result = grid.calculate_expected_profit()
        expected_spacing = 10_000 / 19
        assert abs(result['avg_spacing_usd'] - expected_spacing) < 0.01

    def test_monthly_approximately_daily_times_30(self):
        result = _grid().calculate_expected_profit(fills_per_day=10)
        # Allow for rounding differences from independent rounding of daily/monthly
        assert abs(result['monthly_profit'] - result['daily_profit'] * 30) < 0.50
