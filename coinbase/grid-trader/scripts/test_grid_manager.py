"""
Unit tests for GridManager (Coinbase Exchange, 0.40% maker fee)
"""

import pytest
from grid_manager import GridManager


def make_grid(**overrides):
    defaults = dict(
        min_price=90000,
        max_price=110000,
        grid_levels=21,
        spacing_percent=1.0,
        order_size_usd=50.0
    )
    defaults.update(overrides)
    return GridManager(**defaults)


# ========== Grid Level Construction ==========

class TestGridLevels:
    def test_level_count_matches_grid_levels(self):
        g = make_grid(grid_levels=10)
        assert len(g.levels) == 10

    def test_first_level_is_min_price(self):
        g = make_grid()
        assert g.levels[0] == 90000

    def test_last_level_is_max_price(self):
        g = make_grid()
        assert g.levels[-1] == 110000

    def test_levels_are_evenly_spaced(self):
        g = make_grid(min_price=100, max_price=200, grid_levels=11)
        spacings = [round(g.levels[i+1] - g.levels[i], 6) for i in range(10)]
        assert all(abs(s - spacings[0]) < 0.001 for s in spacings)


# ========== Reference Price ==========

class TestReferencePrice:
    def test_midpoint_of_price_range(self):
        g = make_grid(min_price=90000, max_price=110000)
        assert g.get_reference_price() == pytest.approx(100000)

    def test_asymmetric_range(self):
        g = make_grid(min_price=80000, max_price=100000)
        assert g.get_reference_price() == pytest.approx(90000)


# ========== Required Orders ==========

class TestRequiredOrders:
    def test_buy_orders_below_reference(self):
        g = make_grid()
        ref = g.get_reference_price()
        orders = g.get_required_orders(ref)
        for o in orders['buy']:
            assert o['price'] < ref

    def test_sell_orders_above_reference(self):
        g = make_grid()
        ref = g.get_reference_price()
        orders = g.get_required_orders(ref)
        for o in orders['sell']:
            assert o['price'] > ref

    def test_no_order_at_exact_reference_price(self):
        g = make_grid()
        ref = g.get_reference_price()
        orders = g.get_required_orders(ref)
        all_prices = [o['price'] for o in orders['buy'] + orders['sell']]
        assert ref not in all_prices

    def test_order_size_is_usd_divided_by_price(self):
        g = make_grid(order_size_usd=100.0)
        ref = g.get_reference_price()
        orders = g.get_required_orders(ref)
        sample = orders['buy'][0]
        expected_size = round(100.0 / sample['price'], 8)
        assert sample['size'] == pytest.approx(expected_size)


# ========== Fill Detection ==========

class TestFindFilledOrders:
    def test_detects_filled_orders(self):
        g = make_grid()
        active = {'id1': {}, 'id2': {}, 'id3': {}}
        current_open = {'id2': {}}  # id1 and id3 are gone
        filled = g.find_filled_orders(active, current_open)
        assert set(filled) == {'id1', 'id3'}

    def test_no_fills_when_all_still_open(self):
        g = make_grid()
        active = {'id1': {}, 'id2': {}}
        current_open = {'id1': {}, 'id2': {}}
        assert g.find_filled_orders(active, current_open) == []

    def test_empty_active_orders(self):
        g = make_grid()
        assert g.find_filled_orders({}, {'id1': {}}) == []


# ========== Profit Calculation ==========

class TestCalculateExpectedProfit:
    def test_gross_profit_is_position_based_not_spacing(self):
        """Gross profit = (order_size / avg_price) * avg_spacing, not raw spacing"""
        g = make_grid(min_price=90000, max_price=110000, grid_levels=21, order_size_usd=50.0)
        result = g.calculate_expected_profit()
        # avg_spacing = 1000, avg_price = 100000, buy_qty = 0.0005 BTC
        # gross = 0.0005 * 1000 = $0.50
        assert result['gross_profit_per_cycle'] == pytest.approx(0.50, abs=0.01)

    def test_fees_use_coinbase_maker_rate(self):
        """Fees should reflect 0.40% maker fee, not Kraken's 0.16%"""
        g = make_grid(order_size_usd=50.0)
        result = g.calculate_expected_profit()
        # buy_notional = $50, sell_notional ≈ $50 + ~$0.50; fees ≈ ($50 + $50.50) * 0.40%
        assert result['fees_per_cycle'] == pytest.approx(0.402, abs=0.01)

    def test_net_profit_is_less_than_gross(self):
        g = make_grid()
        result = g.calculate_expected_profit()
        assert result['net_profit_per_cycle'] < result['gross_profit_per_cycle']

    def test_gross_scales_with_order_size(self):
        g1 = make_grid(order_size_usd=50.0)
        g2 = make_grid(order_size_usd=100.0)
        r1 = g1.calculate_expected_profit()
        r2 = g2.calculate_expected_profit()
        assert r2['gross_profit_per_cycle'] == pytest.approx(
            r1['gross_profit_per_cycle'] * 2, rel=0.01
        )

    def test_bankroll_param_affects_return_percent(self):
        g = make_grid()
        r_small = g.calculate_expected_profit(bankroll=500.0)
        r_large = g.calculate_expected_profit(bankroll=1000.0)
        # 2x bankroll → 0.5x return%
        assert r_small['daily_return_percent'] == pytest.approx(
            r_large['daily_return_percent'] * 2, rel=0.01
        )

    def test_monthly_approximately_daily_times_30(self):
        g = make_grid()
        result = g.calculate_expected_profit()
        assert abs(result['monthly_profit'] - result['daily_profit'] * 30) < 0.5

    def test_daily_return_is_realistic(self):
        """Grid trading should not project absurd returns"""
        g = make_grid(
            min_price=90000, max_price=110000,
            grid_levels=21, order_size_usd=50.0
        )
        result = g.calculate_expected_profit(fills_per_day=15, bankroll=1000.0)
        # Realistic: < 5% per day
        assert result['daily_return_percent'] < 5.0

    def test_fees_cover_both_legs(self):
        """Fees should cover both the buy and sell side"""
        g = make_grid(order_size_usd=50.0)
        result = g.calculate_expected_profit()
        # Single-leg fee would be 50 * 0.004 = 0.20; two-leg should be ~2x
        assert result['fees_per_cycle'] > 0.35


# ========== Rebalancing ==========

class TestRebalance:
    def test_should_rebalance_when_price_far_outside_center(self):
        g = make_grid(min_price=90000, max_price=110000)
        # Center = 100000, 15% deviation > 10% threshold
        assert g.should_rebalance_grid(115000) is True

    def test_no_rebalance_within_threshold(self):
        g = make_grid(min_price=90000, max_price=110000)
        assert g.should_rebalance_grid(105000) is False

    def test_rebalanced_grid_centers_on_new_price(self):
        g = make_grid(min_price=90000, max_price=110000)
        new_g = g.rebalance_grid(120000)
        assert new_g.get_reference_price() == pytest.approx(120000)

    def test_rebalanced_grid_preserves_width(self):
        g = make_grid(min_price=90000, max_price=110000)
        new_g = g.rebalance_grid(120000)
        original_width = g.max_price - g.min_price
        new_width = new_g.max_price - new_g.min_price
        assert new_width == pytest.approx(original_width)
