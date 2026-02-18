"""
Kraken Grid Trader Logger - Logs all trading activity

Maintains JSONL log files:
1. grid_setup.jsonl - Grid initialization and updates
2. orders.jsonl - Order placements and cancellations
3. fills.jsonl - Trade executions
4. positions.jsonl - Position changes
5. errors.jsonl - Errors and warnings
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path


class GridTraderLogger:
    """Logs all grid trading activity to JSONL files"""

    def __init__(self, logs_dir: str = 'logs'):
        """
        Initialize grid trader logger

        Args:
            logs_dir: Directory for log files
        """
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(exist_ok=True, parents=True)

    def _append_jsonl(self, filename: str, data: Dict[str, Any]):
        """
        Append a JSON line to a file

        Args:
            filename: Log filename (e.g., 'orders.jsonl')
            data: Data to log
        """
        # Add timestamp if not present
        if 'timestamp' not in data:
            data['timestamp'] = datetime.utcnow().isoformat() + 'Z'

        filepath = self.logs_dir / filename
        with open(filepath, 'a') as f:
            f.write(json.dumps(data) + '\n')

    def log_grid_setup(
        self,
        campaign_name: str,
        pair: str,
        grid_levels: int,
        spacing_percent: float,
        price_range: Dict[str, float],
        status: str,
        error: Optional[str] = None
    ):
        """
        Log grid initialization

        Args:
            campaign_name: Campaign name
            pair: Trading pair (e.g., 'XBTUSD')
            grid_levels: Number of grid levels
            spacing_percent: Grid spacing percentage
            price_range: Min/max price range
            status: 'success' or 'error'
            error: Error message (if failed)
        """
        self._append_jsonl('grid_setup.jsonl', {
            'phase': 'grid_setup',
            'campaign': campaign_name,
            'pair': pair,
            'grid_levels': grid_levels,
            'spacing_percent': spacing_percent,
            'price_range': price_range,
            'status': status,
            'error': error
        })

    def log_order(
        self,
        order_id: str,
        order_type: str,
        side: str,
        price: float,
        volume: float,
        status: str,
        error: Optional[str] = None
    ):
        """
        Log order placement or cancellation

        Args:
            order_id: Order ID from Kraken
            order_type: 'limit' or 'market'
            side: 'buy' or 'sell'
            price: Order price
            volume: Order volume (BTC)
            status: 'placed', 'cancelled', or 'error'
            error: Error message (if failed)
        """
        self._append_jsonl('orders.jsonl', {
            'phase': 'order',
            'order_id': order_id,
            'type': order_type,
            'side': side,
            'price': price,
            'volume': volume,
            'status': status,
            'error': error
        })

    def log_fill(
        self,
        order_id: str,
        side: str,
        price: float,
        volume: float,
        fee: float,
        cost: float
    ):
        """
        Log trade execution

        Args:
            order_id: Order ID that filled
            side: 'buy' or 'sell'
            price: Fill price
            volume: Fill volume (BTC)
            fee: Trading fee (USD)
            cost: Total cost (USD)
        """
        self._append_jsonl('fills.jsonl', {
            'phase': 'fill',
            'order_id': order_id,
            'side': side,
            'price': price,
            'volume': volume,
            'fee': fee,
            'cost': cost
        })

    def log_position_update(
        self,
        pair: str,
        btc_balance: float,
        usd_balance: float,
        total_value_usd: float,
        unrealized_pnl: float,
        open_orders: int
    ):
        """
        Log position snapshot

        Args:
            pair: Trading pair
            btc_balance: Current BTC balance
            usd_balance: Current USD balance
            total_value_usd: Total portfolio value in USD
            unrealized_pnl: Unrealized profit/loss
            open_orders: Number of open orders
        """
        self._append_jsonl('positions.jsonl', {
            'phase': 'position',
            'pair': pair,
            'btc_balance': btc_balance,
            'usd_balance': usd_balance,
            'total_value_usd': total_value_usd,
            'unrealized_pnl': unrealized_pnl,
            'open_orders': open_orders
        })

    def log_error(
        self,
        operation: str,
        error_type: str,
        error_message: str,
        context: Optional[Dict] = None
    ):
        """
        Log error or warning

        Args:
            operation: What operation failed
            error_type: Error category
            error_message: Error details
            context: Additional context
        """
        self._append_jsonl('errors.jsonl', {
            'phase': 'error',
            'operation': operation,
            'error_type': error_type,
            'error_message': error_message,
            'context': context or {}
        })

    def get_recent_logs(self, log_type: str, limit: int = 10) -> list:
        """
        Get recent log entries

        Args:
            log_type: Log type (matches filename without .jsonl)
            limit: Max entries to return

        Returns:
            List of recent log entries
        """
        filepath = self.logs_dir / f"{log_type}.jsonl"
        if not filepath.exists():
            return []

        logs = []
        with open(filepath, 'r') as f:
            for line in f:
                logs.append(json.loads(line))

        # Return most recent entries
        return logs[-limit:]
