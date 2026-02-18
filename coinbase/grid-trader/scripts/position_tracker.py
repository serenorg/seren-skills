"""
Position Tracker - Tracks balances, positions, and P&L

Maintains state of current positions and calculates performance metrics.
Uses base_balance/quote_balance naming to be exchange-agnostic.
"""

import csv
from datetime import datetime
from typing import Dict, List, Optional


class PositionTracker:
    """Tracks trading positions and performance"""

    def __init__(self, initial_bankroll: float, product_id: str):
        """
        Initialize position tracker

        Args:
            initial_bankroll: Starting capital (USD)
            product_id: Trading pair (e.g., 'BTC-USD')
        """
        self.initial_bankroll = initial_bankroll
        self.product_id = product_id
        self.base_currency = product_id.split('-')[0]   # e.g., 'BTC'
        self.quote_currency = product_id.split('-')[1]  # e.g., 'USD'
        self.base_balance = 0.0
        self.quote_balance = initial_bankroll
        self.open_orders: Dict[str, Dict] = {}
        self.filled_orders: List[Dict] = []
        self.total_fees_paid = 0.0
        self.total_volume_traded = 0.0
        self.start_time = datetime.utcnow()

    def update_balances(self, base_balance: float, quote_balance: float):
        """
        Update current balances from Coinbase

        Args:
            base_balance: Current base currency balance (e.g., BTC)
            quote_balance: Current quote currency balance (e.g., USD)
        """
        self.base_balance = base_balance
        self.quote_balance = quote_balance

    def add_open_order(self, order_id: str, order_details: Dict):
        """
        Track a newly placed order

        Args:
            order_id: Order ID from Coinbase
            order_details: Order details (price, size, side)
        """
        self.open_orders[order_id] = {
            **order_details,
            'placed_at': datetime.utcnow().isoformat()
        }

    def remove_open_order(self, order_id: str):
        """Remove order from open orders (cancelled or filled)"""
        self.open_orders.pop(order_id, None)

    def record_fill(
        self,
        order_id: str,
        side: str,
        price: float,
        size: float,
        fee: float,
        cost: float
    ):
        """
        Record a filled order

        Args:
            order_id: Order ID that filled
            side: 'buy' or 'sell'
            price: Fill price (USD)
            size: Fill size in base currency
            fee: Trading fee (USD)
            cost: Total cost (USD)
        """
        self.filled_orders.append({
            'order_id': order_id,
            'side': side,
            'price': price,
            'size': size,
            'fee': fee,
            'cost': cost,
            'filled_at': datetime.utcnow().isoformat()
        })
        self.total_fees_paid += fee
        self.total_volume_traded += cost
        self.remove_open_order(order_id)

    def get_current_value(self, base_price: float) -> float:
        """
        Calculate current portfolio value in USD

        Args:
            base_price: Current base currency price (USD)

        Returns:
            Total portfolio value (USD)
        """
        return self.quote_balance + (self.base_balance * base_price)

    def get_unrealized_pnl(self, base_price: float) -> float:
        """Unrealized P&L vs initial bankroll"""
        return self.get_current_value(base_price) - self.initial_bankroll

    def get_realized_pnl(self) -> float:
        """Realized P&L from completed buy/sell pairs"""
        if len(self.filled_orders) < 2:
            return 0.0

        buys = [f for f in self.filled_orders if f['side'] == 'buy']
        sells = [f for f in self.filled_orders if f['side'] == 'sell']

        realized = 0.0
        for buy, sell in zip(buys, sells):
            profit = (sell['price'] - buy['price']) * buy['size']
            realized += profit - buy['fee'] - sell['fee']
        return realized

    def get_performance_metrics(self, base_price: float) -> Dict:
        """
        Get comprehensive performance metrics

        Args:
            base_price: Current base currency price (USD)

        Returns:
            Dict with performance stats
        """
        current_value = self.get_current_value(base_price)
        unrealized_pnl = self.get_unrealized_pnl(base_price)
        realized_pnl = self.get_realized_pnl()
        total_pnl = realized_pnl + unrealized_pnl
        roi_percent = (total_pnl / self.initial_bankroll) * 100

        num_fills = len(self.filled_orders)
        avg_fee = self.total_fees_paid / num_fills if num_fills > 0 else 0.0

        elapsed_hours = (datetime.utcnow() - self.start_time).total_seconds() / 3600
        fills_per_hour = num_fills / elapsed_hours if elapsed_hours > 0 else 0.0

        return {
            'initial_bankroll': round(self.initial_bankroll, 2),
            'current_value': round(current_value, 2),
            'base_balance': round(self.base_balance, 8),
            'quote_balance': round(self.quote_balance, 2),
            'unrealized_pnl': round(unrealized_pnl, 2),
            'realized_pnl': round(realized_pnl, 2),
            'total_pnl': round(total_pnl, 2),
            'roi_percent': round(roi_percent, 2),
            'num_fills': num_fills,
            'total_fees_paid': round(self.total_fees_paid, 2),
            'total_volume_traded': round(self.total_volume_traded, 2),
            'avg_fee_per_trade': round(avg_fee, 2),
            'open_orders': len(self.open_orders),
            'fills_per_hour': round(fills_per_hour, 2),
            'elapsed_hours': round(elapsed_hours, 2),
        }

    def should_stop_loss(self, base_price: float, stop_loss_threshold: float) -> bool:
        """
        Check if stop-loss threshold has been breached

        Args:
            base_price: Current base currency price (USD)
            stop_loss_threshold: Minimum portfolio value (USD) to continue trading

        Returns:
            True if should stop trading
        """
        return self.get_current_value(base_price) < stop_loss_threshold

    def get_position_summary(self, base_price: float) -> str:
        """Human-readable position summary"""
        m = self.get_performance_metrics(base_price)
        base = self.base_currency
        return f"""
============================================================
POSITION SUMMARY
============================================================
Initial Bankroll:    ${m['initial_bankroll']:,.2f}
Current Value:       ${m['current_value']:,.2f}
{base} Balance:          {m['base_balance']:.8f} {base}
USD Balance:         ${m['quote_balance']:,.2f}

Unrealized P&L:      ${m['unrealized_pnl']:,.2f}
Realized P&L:        ${m['realized_pnl']:,.2f}
Total P&L:           ${m['total_pnl']:,.2f}
ROI:                 {m['roi_percent']:.2f}%

Total Fills:         {m['num_fills']}
Open Orders:         {m['open_orders']}
Total Fees Paid:     ${m['total_fees_paid']:,.2f}
Avg Fee/Trade:       ${m['avg_fee_per_trade']:.2f}

Fills/Hour:          {m['fills_per_hour']:.2f}
Elapsed Time:        {m['elapsed_hours']:.1f} hours
============================================================
"""

    def export_fills_to_csv(self, output_path: str):
        """
        Export filled orders to CSV for analysis

        Args:
            output_path: Path to output CSV file
        """
        if not self.filled_orders:
            return

        with open(output_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=self.filled_orders[0].keys())
            writer.writeheader()
            writer.writerows(self.filled_orders)
