"""
Position Tracker - Tracks balances, positions, and P&L

Maintains state of current positions and calculates performance metrics
"""

from typing import Dict, List, Optional
from datetime import datetime


class PositionTracker:
    """Tracks trading positions and performance"""

    def __init__(self, initial_bankroll: float):
        """
        Initialize position tracker

        Args:
            initial_bankroll: Starting capital (USD)
        """
        self.initial_bankroll = initial_bankroll
        self.btc_balance = 0.0
        self.usd_balance = initial_bankroll
        self.open_orders = {}  # order_id -> order_details
        self.filled_orders = []  # List of filled order records
        self.total_fees_paid = 0.0
        self.total_volume_traded = 0.0
        self.start_time = datetime.utcnow()

    def update_balances(self, btc_balance: float, usd_balance: float):
        """
        Update current balances from Kraken

        Args:
            btc_balance: Current BTC balance
            usd_balance: Current USD balance
        """
        self.btc_balance = btc_balance
        self.usd_balance = usd_balance

    def add_open_order(self, order_id: str, order_details: Dict):
        """
        Track a newly placed order

        Args:
            order_id: Order ID from Kraken
            order_details: Order details (price, volume, side)
        """
        self.open_orders[order_id] = {
            **order_details,
            'placed_at': datetime.utcnow().isoformat()
        }

    def remove_open_order(self, order_id: str):
        """
        Remove order from open orders (cancelled or filled)

        Args:
            order_id: Order ID to remove
        """
        if order_id in self.open_orders:
            del self.open_orders[order_id]

    def record_fill(
        self,
        order_id: str,
        side: str,
        price: float,
        volume: float,
        fee: float,
        cost: float
    ):
        """
        Record a filled order

        Args:
            order_id: Order ID that filled
            side: 'buy' or 'sell'
            price: Fill price
            volume: Fill volume (BTC)
            fee: Trading fee (USD)
            cost: Total cost (USD)
        """
        fill_record = {
            'order_id': order_id,
            'side': side,
            'price': price,
            'volume': volume,
            'fee': fee,
            'cost': cost,
            'filled_at': datetime.utcnow().isoformat()
        }

        self.filled_orders.append(fill_record)
        self.total_fees_paid += fee
        self.total_volume_traded += cost
        self.remove_open_order(order_id)

    def get_current_value(self, btc_price: float) -> float:
        """
        Calculate current portfolio value in USD

        Args:
            btc_price: Current BTC price

        Returns:
            Total portfolio value (USD)
        """
        btc_value = self.btc_balance * btc_price
        return self.usd_balance + btc_value

    def get_unrealized_pnl(self, btc_price: float) -> float:
        """
        Calculate unrealized profit/loss

        Args:
            btc_price: Current BTC price

        Returns:
            Unrealized P&L (USD)
        """
        current_value = self.get_current_value(btc_price)
        return current_value - self.initial_bankroll

    def get_realized_pnl(self) -> float:
        """
        Calculate realized profit/loss from completed trades

        Returns:
            Realized P&L (USD)
        """
        if len(self.filled_orders) < 2:
            return 0.0

        # Match buy/sell pairs to calculate realized P&L
        buys = [f for f in self.filled_orders if f['side'] == 'buy']
        sells = [f for f in self.filled_orders if f['side'] == 'sell']

        realized_pnl = 0.0
        for i in range(min(len(buys), len(sells))):
            buy = buys[i]
            sell = sells[i]
            # Profit = (sell price - buy price) * volume - fees
            profit = (sell['price'] - buy['price']) * buy['volume']
            fees = buy['fee'] + sell['fee']
            realized_pnl += (profit - fees)

        return realized_pnl

    def get_performance_metrics(self, btc_price: float) -> Dict:
        """
        Get comprehensive performance metrics

        Args:
            btc_price: Current BTC price

        Returns:
            Dict with performance stats
        """
        current_value = self.get_current_value(btc_price)
        unrealized_pnl = self.get_unrealized_pnl(btc_price)
        realized_pnl = self.get_realized_pnl()
        total_pnl = realized_pnl + unrealized_pnl

        # Calculate returns
        roi_percent = (total_pnl / self.initial_bankroll) * 100

        # Calculate trading activity
        num_fills = len(self.filled_orders)
        avg_fee_per_trade = self.total_fees_paid / num_fills if num_fills > 0 else 0

        # Calculate time-based metrics
        elapsed_hours = (datetime.utcnow() - self.start_time).total_seconds() / 3600
        fills_per_hour = num_fills / elapsed_hours if elapsed_hours > 0 else 0

        return {
            'initial_bankroll': round(self.initial_bankroll, 2),
            'current_value': round(current_value, 2),
            'btc_balance': round(self.btc_balance, 8),
            'usd_balance': round(self.usd_balance, 2),
            'unrealized_pnl': round(unrealized_pnl, 2),
            'realized_pnl': round(realized_pnl, 2),
            'total_pnl': round(total_pnl, 2),
            'roi_percent': round(roi_percent, 2),
            'num_fills': num_fills,
            'total_fees_paid': round(self.total_fees_paid, 2),
            'total_volume_traded': round(self.total_volume_traded, 2),
            'avg_fee_per_trade': round(avg_fee_per_trade, 2),
            'open_orders': len(self.open_orders),
            'fills_per_hour': round(fills_per_hour, 2),
            'elapsed_hours': round(elapsed_hours, 2)
        }

    def should_stop_loss(self, btc_price: float, stop_loss_threshold: float) -> bool:
        """
        Check if stop-loss threshold has been breached

        Args:
            btc_price: Current BTC price
            stop_loss_threshold: Minimum bankroll value (USD)

        Returns:
            True if should stop trading
        """
        current_value = self.get_current_value(btc_price)
        return current_value < stop_loss_threshold

    def get_position_summary(self, btc_price: float) -> str:
        """
        Get human-readable position summary

        Args:
            btc_price: Current BTC price

        Returns:
            Formatted summary string
        """
        metrics = self.get_performance_metrics(btc_price)

        summary = f"""
============================================================
POSITION SUMMARY
============================================================
Initial Bankroll:    ${metrics['initial_bankroll']:,.2f}
Current Value:       ${metrics['current_value']:,.2f}
BTC Balance:         {metrics['btc_balance']:.8f} BTC
USD Balance:         ${metrics['usd_balance']:,.2f}

Unrealized P&L:      ${metrics['unrealized_pnl']:,.2f}
Realized P&L:        ${metrics['realized_pnl']:,.2f}
Total P&L:           ${metrics['total_pnl']:,.2f}
ROI:                 {metrics['roi_percent']:.2f}%

Total Fills:         {metrics['num_fills']}
Open Orders:         {metrics['open_orders']}
Total Fees Paid:     ${metrics['total_fees_paid']:,.2f}
Avg Fee/Trade:       ${metrics['avg_fee_per_trade']:.2f}

Fills/Hour:          {metrics['fills_per_hour']:.2f}
Elapsed Time:        {metrics['elapsed_hours']:.1f} hours
============================================================
"""
        return summary

    def export_fills_to_csv(self, output_path: str):
        """
        Export filled orders to CSV for analysis

        Args:
            output_path: Path to output CSV file
        """
        import csv

        with open(output_path, 'w', newline='') as f:
            if not self.filled_orders:
                return

            fieldnames = self.filled_orders[0].keys()
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.filled_orders)
