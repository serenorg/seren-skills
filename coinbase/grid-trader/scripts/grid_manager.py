"""
Grid Manager - Calculates and manages grid trading levels

Handles grid construction, order placement logic, and grid updates.
Uses arithmetic spacing between price levels.
"""

from typing import Dict, List, Optional


class GridManager:
    """Manages grid trading logic for Coinbase Exchange"""

    # Coinbase Exchange maker fee for < $10K 30-day volume
    MAKER_FEE_RATE = 0.0040

    def __init__(
        self,
        min_price: float,
        max_price: float,
        grid_levels: int,
        spacing_percent: float,
        order_size_usd: float
    ):
        """
        Initialize grid manager

        Args:
            min_price: Minimum grid price (USD)
            max_price: Maximum grid price (USD)
            grid_levels: Number of grid levels
            spacing_percent: Spacing between levels (e.g., 2.0 for 2%)
            order_size_usd: Order size in USD per level
        """
        self.min_price = min_price
        self.max_price = max_price
        self.grid_levels = grid_levels
        self.spacing_percent = spacing_percent
        self.order_size_usd = order_size_usd
        self.levels = self._calculate_grid_levels()

    def _calculate_grid_levels(self) -> List[float]:
        """
        Calculate evenly spaced grid price levels

        Returns:
            List of price levels from min to max
        """
        step = (self.max_price - self.min_price) / (self.grid_levels - 1)
        return [round(self.min_price + i * step, 2) for i in range(self.grid_levels)]

    def get_reference_price(self) -> float:
        """
        Get reference price for initial grid placement.

        Coinbase Exchange does not expose a ticker endpoint via the current
        publisher configuration. The midpoint of the configured price range
        is used as a proxy for the current market price when placing the
        initial grid. Update price_range in config to center the grid.

        Returns:
            Midpoint of the configured price range
        """
        return (self.min_price + self.max_price) / 2

    def get_required_orders(self, reference_price: float) -> Dict[str, List[Dict]]:
        """
        Determine which orders should be active based on reference price

        Args:
            reference_price: Reference price for grid placement

        Returns:
            Dict with 'buy' and 'sell' order lists
        """
        buy_orders = []
        sell_orders = []

        for level in self.levels:
            size = self.order_size_usd / level
            order = {'price': level, 'size': round(size, 8), 'side': ''}

            if level < reference_price:
                order['side'] = 'buy'
                buy_orders.append(order)
            elif level > reference_price:
                order['side'] = 'sell'
                sell_orders.append(order)

        return {'buy': buy_orders, 'sell': sell_orders}

    def find_filled_orders(
        self,
        active_orders: Dict[str, Dict],
        current_open_orders: Dict[str, Dict]
    ) -> List[str]:
        """
        Find orders that have been filled (no longer in open order list)

        Args:
            active_orders: Order IDs we previously placed (id -> details)
            current_open_orders: Currently open orders from Coinbase (id -> details)

        Returns:
            List of filled order IDs
        """
        return [
            order_id for order_id in active_orders
            if order_id not in current_open_orders
        ]

    def calculate_order_size(self, price: float) -> float:
        """
        Calculate order size in base currency for a given price

        Args:
            price: Order price (USD)

        Returns:
            Size in base currency (e.g., BTC)
        """
        return round(self.order_size_usd / price, 8)

    def get_grid_stats(self, reference_price: float) -> Dict:
        """
        Get grid statistics

        Args:
            reference_price: Current or reference price

        Returns:
            Dict with grid stats
        """
        return {
            'total_levels': len(self.levels),
            'levels_below': sum(1 for level in self.levels if level < reference_price),
            'levels_above': sum(1 for level in self.levels if level > reference_price),
            'min_price': self.min_price,
            'max_price': self.max_price,
            'reference_price': reference_price,
            'spacing_percent': self.spacing_percent,
            'order_size_usd': self.order_size_usd,
        }

    def should_rebalance_grid(
        self,
        reference_price: float,
        threshold_percent: float = 10.0
    ) -> bool:
        """
        Check if grid should be rebalanced (price moved too far from center)

        Args:
            reference_price: Current reference price
            threshold_percent: Rebalance if price moves this % from center

        Returns:
            True if rebalance needed
        """
        center = (self.min_price + self.max_price) / 2
        deviation = abs(reference_price - center) / center * 100
        return deviation > threshold_percent

    def rebalance_grid(self, new_center_price: float) -> 'GridManager':
        """
        Create new grid centered on new price, maintaining same total width

        Args:
            new_center_price: New center price

        Returns:
            New GridManager instance with updated range
        """
        half_width = (self.max_price - self.min_price) / 2
        return GridManager(
            min_price=new_center_price - half_width,
            max_price=new_center_price + half_width,
            grid_levels=self.grid_levels,
            spacing_percent=self.spacing_percent,
            order_size_usd=self.order_size_usd
        )

    def get_next_buy_level(self, reference_price: float) -> Optional[float]:
        """Get the highest buy level below reference price"""
        candidates = [level for level in self.levels if level < reference_price]
        return max(candidates) if candidates else None

    def get_next_sell_level(self, reference_price: float) -> Optional[float]:
        """Get the lowest sell level above reference price"""
        candidates = [level for level in self.levels if level > reference_price]
        return min(candidates) if candidates else None

    def calculate_expected_profit(
        self,
        fills_per_day: int = 15,
        bankroll: Optional[float] = None
    ) -> Dict:
        """
        Calculate expected profit metrics

        Args:
            fills_per_day: Expected number of fills per day
            bankroll: Total capital deployed (defaults to order_size_usd * grid_levels)

        Returns:
            Dict with profit projections
        """
        avg_spacing = (self.max_price - self.min_price) / (self.grid_levels - 1)
        avg_price = (self.min_price + self.max_price) / 2

        # Profit per grid cycle: buy qty * spacing
        buy_qty = self.order_size_usd / avg_price
        gross_profit_per_cycle = buy_qty * avg_spacing

        # Two-leg fees: 0.40% maker on buy notional + sell notional
        buy_notional = self.order_size_usd
        sell_notional = buy_qty * (avg_price + avg_spacing)
        fees_per_cycle = (buy_notional + sell_notional) * self.MAKER_FEE_RATE

        net_profit_per_cycle = gross_profit_per_cycle - fees_per_cycle
        daily_profit = net_profit_per_cycle * fills_per_day
        monthly_profit = daily_profit * 30

        capital = bankroll if bankroll is not None else self.order_size_usd * self.grid_levels

        return {
            'avg_spacing_usd': round(avg_spacing, 2),
            'gross_profit_per_cycle': round(gross_profit_per_cycle, 4),
            'fees_per_cycle': round(fees_per_cycle, 4),
            'net_profit_per_cycle': round(net_profit_per_cycle, 4),
            'daily_profit': round(daily_profit, 2),
            'monthly_profit': round(monthly_profit, 2),
            'daily_return_percent': round(daily_profit / capital * 100, 4),
            'monthly_return_percent': round(monthly_profit / capital * 100, 2),
        }
