"""
Grid Manager - Calculates and manages grid trading levels

Handles grid construction, order placement logic, and grid updates
"""

from copy import deepcopy
from typing import Dict, List, Tuple, Optional
import math


DEFAULT_BACKTEST_SETTINGS = {
    "auto_optimize_on_invoke": True,
    "bankroll_usd": 100.0,
    "target_pnl_pct": 25.0,
    "horizon_days": 30,
    "fills_per_day_candidates": [12, 15, 20, 25],
    "grid_levels_candidates": [10, 12, 16, 20],
    "spacing_percent_candidates": [1.0, 2.0, 3.0, 4.0],
    "order_size_percent_candidates": [5.0, 10.0, 15.0, 20.0],
    "price_range_scale_candidates": [0.8, 1.0, 1.2],
    "stop_loss_buffer_pct": 20.0,
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def resolve_backtest_settings(config: dict) -> dict:
    raw = config.get("backtest", {})
    if not isinstance(raw, dict):
        raw = {}
    settings = _deep_merge(DEFAULT_BACKTEST_SETTINGS, raw)
    settings["bankroll_usd"] = float(settings.get("bankroll_usd", 100.0))
    settings["target_pnl_pct"] = float(settings.get("target_pnl_pct", 25.0))
    settings["horizon_days"] = int(settings.get("horizon_days", 30))
    settings["auto_optimize_on_invoke"] = bool(settings.get("auto_optimize_on_invoke", True))
    settings["stop_loss_buffer_pct"] = float(settings.get("stop_loss_buffer_pct", 20.0))
    return settings


def optimize_backtest_configuration(config: dict) -> dict:
    settings = resolve_backtest_settings(config)
    strategy = deepcopy(config.get("strategy", {}))
    risk_management = deepcopy(config.get("risk_management", {}))
    price_range = strategy.get("price_range", {})
    minimum = float(price_range.get("min", 0.0))
    maximum = float(price_range.get("max", 0.0))
    center = (minimum + maximum) / 2.0 if minimum and maximum else 0.0
    width = max(maximum - minimum, 1.0)
    bankroll = max(float(settings["bankroll_usd"]), 1.0)

    attempts = 0
    best_attempt = None

    for scale in settings.get("price_range_scale_candidates", []):
        half_width = max((width * float(scale)) / 2.0, 1.0)
        scaled_range = {
            "min": round(center - half_width, 2),
            "max": round(center + half_width, 2),
        }
        for grid_levels in settings.get("grid_levels_candidates", []):
            for spacing_percent in settings.get("spacing_percent_candidates", []):
                for order_size_percent in settings.get("order_size_percent_candidates", []):
                    order_size_usd = bankroll * (float(order_size_percent) / 100.0)
                    grid = GridManager(
                        min_price=scaled_range["min"],
                        max_price=scaled_range["max"],
                        grid_levels=int(grid_levels),
                        spacing_percent=float(spacing_percent),
                        order_size_usd=order_size_usd,
                    )
                    for fills_per_day in settings.get("fills_per_day_candidates", []):
                        expected = grid.calculate_expected_profit(
                            fills_per_day=int(fills_per_day),
                            bankroll=bankroll,
                        )
                        attempts += 1
                        candidate = {
                            "modeled_pnl_pct": float(expected["monthly_return_percent"]),
                            "fills_per_day_assumption": int(fills_per_day),
                            "selected_config": {
                                "strategy": {
                                    "bankroll": round(bankroll, 2),
                                    "grid_levels": int(grid_levels),
                                    "grid_spacing_percent": float(spacing_percent),
                                    "order_size_percent": float(order_size_percent),
                                    "price_range": scaled_range,
                                    "scan_interval_seconds": int(strategy.get("scan_interval_seconds", 60)),
                                },
                                "risk_management": {
                                    **risk_management,
                                    "stop_loss_bankroll": round(
                                        bankroll * (1.0 - (float(settings["stop_loss_buffer_pct"]) / 100.0)),
                                        2,
                                    ),
                                },
                            },
                            "expected": expected,
                        }
                        if best_attempt is None or candidate["modeled_pnl_pct"] > best_attempt["modeled_pnl_pct"]:
                            best_attempt = candidate

    if best_attempt is None:
        return {
            "config": deepcopy(config),
            "summary": {
                "applied": False,
                "target_met": False,
                "attempt_count": 0,
                "bankroll_usd": bankroll,
                "target_pnl_pct": settings["target_pnl_pct"],
                "modeled_pnl_pct": 0.0,
                "selected_targets": {
                    "trading_pair": config.get("trading_pair"),
                    "pairs": list(config.get("pairs", [])),
                },
                "selected_config": {},
            },
        }

    updated = deepcopy(config)
    updated["strategy"] = _deep_merge(updated.get("strategy", {}), best_attempt["selected_config"]["strategy"])
    updated["risk_management"] = _deep_merge(
        updated.get("risk_management", {}),
        best_attempt["selected_config"]["risk_management"],
    )
    updated["backtest"] = _deep_merge(
        settings,
        {
            "selected_config": best_attempt["selected_config"],
            "selected_targets": {
                "trading_pair": updated.get("trading_pair"),
                "pairs": list(updated.get("pairs", [])),
            },
            "last_modeled_pnl_pct": round(best_attempt["modeled_pnl_pct"], 4),
            "last_attempt_count": attempts,
            "last_target_met": best_attempt["modeled_pnl_pct"] >= float(settings["target_pnl_pct"]),
        },
    )
    return {
        "config": updated,
        "summary": {
            "applied": True,
            "bankroll_usd": round(bankroll, 2),
            "target_pnl_pct": float(settings["target_pnl_pct"]),
            "target_met": best_attempt["modeled_pnl_pct"] >= float(settings["target_pnl_pct"]),
            "attempt_count": attempts,
            "modeled_pnl_pct": round(best_attempt["modeled_pnl_pct"], 4),
            "selected_targets": {
                "trading_pair": updated.get("trading_pair"),
                "pairs": list(updated.get("pairs", [])),
            },
            "selected_config": best_attempt["selected_config"],
            "expected": best_attempt["expected"],
            "horizon_days": int(settings["horizon_days"]),
        },
    }


class GridManager:
    """Manages grid trading logic"""

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

        # Calculate grid levels
        self.levels = self._calculate_grid_levels()

    def _calculate_grid_levels(self) -> List[float]:
        """
        Calculate evenly spaced grid price levels

        Returns:
            List of price levels from min to max
        """
        # Use arithmetic spacing for simplicity
        levels = []
        step = (self.max_price - self.min_price) / (self.grid_levels - 1)

        for i in range(self.grid_levels):
            price = self.min_price + (i * step)
            levels.append(round(price, 2))

        return levels

    def get_required_orders(self, current_price: float) -> Dict[str, List[Dict]]:
        """
        Determine which orders should be active based on current price

        Args:
            current_price: Current market price

        Returns:
            Dict with 'buy' and 'sell' order lists
        """
        buy_orders = []
        sell_orders = []

        for level in self.levels:
            # Place buy orders below current price
            if level < current_price:
                volume = self.order_size_usd / level
                buy_orders.append({
                    'price': level,
                    'volume': round(volume, 8),
                    'side': 'buy'
                })

            # Place sell orders above current price
            elif level > current_price:
                volume = self.order_size_usd / level
                sell_orders.append({
                    'price': level,
                    'volume': round(volume, 8),
                    'side': 'sell'
                })

        return {
            'buy': buy_orders,
            'sell': sell_orders
        }

    def find_filled_orders(
        self,
        active_orders: Dict[str, Dict],
        current_open_orders: Dict[str, Dict]
    ) -> List[str]:
        """
        Find orders that have been filled (no longer open)

        Args:
            active_orders: Dict of order IDs we previously placed
            current_open_orders: Dict of currently open orders from Kraken

        Returns:
            List of filled order IDs
        """
        filled_order_ids = []

        for order_id in active_orders.keys():
            if order_id not in current_open_orders:
                filled_order_ids.append(order_id)

        return filled_order_ids

    def calculate_order_volume(self, price: float) -> float:
        """
        Calculate order volume (BTC) for a given price

        Args:
            price: Order price (USD)

        Returns:
            Volume in BTC
        """
        volume = self.order_size_usd / price
        return round(volume, 8)

    def get_grid_stats(self, current_price: float) -> Dict:
        """
        Get grid statistics

        Args:
            current_price: Current market price

        Returns:
            Dict with grid stats
        """
        levels_below = sum(1 for level in self.levels if level < current_price)
        levels_above = sum(1 for level in self.levels if level > current_price)

        return {
            'total_levels': len(self.levels),
            'levels_below': levels_below,
            'levels_above': levels_above,
            'min_price': self.min_price,
            'max_price': self.max_price,
            'current_price': current_price,
            'spacing_percent': self.spacing_percent,
            'order_size_usd': self.order_size_usd
        }

    def should_rebalance_grid(
        self,
        current_price: float,
        threshold_percent: float = 10.0
    ) -> bool:
        """
        Check if grid should be rebalanced (price moved too far)

        Args:
            current_price: Current market price
            threshold_percent: Rebalance if price moves this % from center

        Returns:
            True if rebalance needed
        """
        grid_center = (self.min_price + self.max_price) / 2
        price_deviation_percent = abs(current_price - grid_center) / grid_center * 100

        return price_deviation_percent > threshold_percent

    def rebalance_grid(self, new_center_price: float) -> 'GridManager':
        """
        Create new grid centered on new price

        Args:
            new_center_price: New center price for grid

        Returns:
            New GridManager instance with updated range
        """
        # Calculate new range maintaining same total width
        total_width = self.max_price - self.min_price
        new_min = new_center_price - (total_width / 2)
        new_max = new_center_price + (total_width / 2)

        return GridManager(
            min_price=new_min,
            max_price=new_max,
            grid_levels=self.grid_levels,
            spacing_percent=self.spacing_percent,
            order_size_usd=self.order_size_usd
        )

    def get_next_buy_level(self, current_price: float) -> Optional[float]:
        """
        Get next buy level below current price

        Args:
            current_price: Current market price

        Returns:
            Next buy level price, or None if none exist
        """
        buy_levels = [level for level in self.levels if level < current_price]
        return max(buy_levels) if buy_levels else None

    def get_next_sell_level(self, current_price: float) -> Optional[float]:
        """
        Get next sell level above current price

        Args:
            current_price: Current market price

        Returns:
            Next sell level price, or None if none exist
        """
        sell_levels = [level for level in self.levels if level > current_price]
        return min(sell_levels) if sell_levels else None

    def calculate_expected_profit(
        self,
        fills_per_day: int = 15,
        bankroll: Optional[float] = None
    ) -> Dict:
        """
        Calculate expected profit metrics for one complete grid cycle.

        A cycle = buy Q units at avg_buy_price, sell Q units one level higher.

        Args:
            fills_per_day: Expected number of round-trip fills per day
            bankroll: Total bankroll in USD (used for return % if provided)

        Returns:
            Dict with profit projections
        """
        fee_rate = 0.0016  # 0.16% Kraken maker fee

        # Average spacing and mid-price across the grid
        avg_spacing = (self.max_price - self.min_price) / (self.grid_levels - 1)
        avg_price = (self.min_price + self.max_price) / 2

        # Quantity bought per order at the average price
        buy_qty = self.order_size_usd / avg_price

        # Gross profit: sell the same qty one grid level higher
        gross_profit_per_cycle = buy_qty * avg_spacing

        # Fees: buy notional + sell notional (sell is slightly higher)
        buy_notional = self.order_size_usd
        sell_notional = buy_qty * (avg_price + avg_spacing)
        fees_per_cycle = (buy_notional + sell_notional) * fee_rate

        net_profit_per_cycle = gross_profit_per_cycle - fees_per_cycle

        # Daily/monthly projections
        daily_profit = net_profit_per_cycle * fills_per_day
        monthly_profit = daily_profit * 30

        # Return % against bankroll (if provided) or total capital deployed
        capital = bankroll if bankroll is not None else self.order_size_usd * self.grid_levels

        return {
            'avg_spacing_usd': round(avg_spacing, 2),
            'gross_profit_per_cycle': round(gross_profit_per_cycle, 4),
            'fees_per_cycle': round(fees_per_cycle, 4),
            'net_profit_per_cycle': round(net_profit_per_cycle, 4),
            'daily_profit': round(daily_profit, 2),
            'monthly_profit': round(monthly_profit, 2),
            'daily_return_percent': round(daily_profit / capital * 100, 4),
            'monthly_return_percent': round(monthly_profit / capital * 100, 2)
        }
