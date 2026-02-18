"""
Coinbase Grid Trader Logger - Logs all trading activity to JSONL files

Log files:
1. grid_setup.jsonl  - Grid initialization and updates
2. orders.jsonl      - Order placements and cancellations
3. fills.jsonl       - Trade executions
4. positions.jsonl   - Position snapshots
5. errors.jsonl      - Errors and warnings
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class GridTraderLogger:
    """Logs all grid trading activity to JSONL files"""

    def __init__(self, logs_dir: str = 'logs'):
        """
        Initialize logger

        Args:
            logs_dir: Directory for log files
        """
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(exist_ok=True, parents=True)

    def _append_jsonl(self, filename: str, data: Dict[str, Any]):
        """Append a JSON line to a log file"""
        if 'timestamp' not in data:
            data['timestamp'] = datetime.utcnow().isoformat() + 'Z'
        filepath = self.logs_dir / filename
        with open(filepath, 'a') as f:
            f.write(json.dumps(data) + '\n')

    def log_grid_setup(
        self,
        campaign_name: str,
        product_id: str,
        grid_levels: int,
        spacing_percent: float,
        price_range: Dict[str, float],
        status: str,
        error: Optional[str] = None
    ):
        """Log grid initialization"""
        self._append_jsonl('grid_setup.jsonl', {
            'phase': 'grid_setup',
            'campaign': campaign_name,
            'product_id': product_id,
            'grid_levels': grid_levels,
            'spacing_percent': spacing_percent,
            'price_range': price_range,
            'status': status,
            'error': error,
        })

    def log_order(
        self,
        order_id: str,
        side: str,
        price: float,
        size: float,
        status: str,
        error: Optional[str] = None
    ):
        """Log order placement or cancellation"""
        self._append_jsonl('orders.jsonl', {
            'phase': 'order',
            'order_id': order_id,
            'side': side,
            'price': price,
            'size': size,
            'status': status,
            'error': error,
        })

    def log_fill(
        self,
        order_id: str,
        side: str,
        price: float,
        size: float,
        fee: float,
        cost: float
    ):
        """Log trade execution"""
        self._append_jsonl('fills.jsonl', {
            'phase': 'fill',
            'order_id': order_id,
            'side': side,
            'price': price,
            'size': size,
            'fee': fee,
            'cost': cost,
        })

    def log_position_update(
        self,
        product_id: str,
        base_balance: float,
        quote_balance: float,
        total_value_usd: float,
        unrealized_pnl: float,
        open_orders: int
    ):
        """Log position snapshot"""
        self._append_jsonl('positions.jsonl', {
            'phase': 'position',
            'product_id': product_id,
            'base_balance': base_balance,
            'quote_balance': quote_balance,
            'total_value_usd': total_value_usd,
            'unrealized_pnl': unrealized_pnl,
            'open_orders': open_orders,
        })

    def log_error(
        self,
        operation: str,
        error_type: str,
        error_message: str,
        context: Optional[Dict] = None
    ):
        """Log error or warning"""
        self._append_jsonl('errors.jsonl', {
            'phase': 'error',
            'operation': operation,
            'error_type': error_type,
            'error_message': error_message,
            'context': context or {},
        })

    def get_recent_logs(self, log_type: str, limit: int = 10) -> list:
        """
        Get recent log entries

        Args:
            log_type: Log type ('fills', 'orders', 'positions', 'errors', 'grid_setup')
            limit: Max entries to return
        """
        filepath = self.logs_dir / f'{log_type}.jsonl'
        if not filepath.exists():
            return []
        entries = []
        with open(filepath, 'r') as f:
            for line in f:
                entries.append(json.loads(line))
        return entries[-limit:]
