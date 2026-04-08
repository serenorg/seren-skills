"""
Trading Logger - Logs all Kalshi trading activity

Maintains three log files:
1. logs/trades.jsonl - One line per trade (opened/closed)
2. logs/scan_results.jsonl - One line per scan cycle
3. logs/notifications.jsonl - Critical events for user notification
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional


class TradingLogger:
    """Logs all trading activity to JSONL files"""

    def __init__(
        self,
        log_dir: str = 'logs',
    ):
        """
        Initialize trading logger.

        Args:
            log_dir: Directory for log files
        """
        self.log_dir = log_dir
        self.trades_log = os.path.join(log_dir, 'trades.jsonl')
        self.scans_log = os.path.join(log_dir, 'scan_results.jsonl')
        self.notifications_log = os.path.join(log_dir, 'notifications.jsonl')

        os.makedirs(log_dir, exist_ok=True)

    def _append_jsonl(self, filepath: str, data: Dict[str, Any]):
        """Append a JSON line to a file"""
        if 'timestamp' not in data:
            data['timestamp'] = datetime.now(timezone.utc).isoformat()

        with open(filepath, 'a') as f:
            f.write(json.dumps(data) + '\n')

    def log_trade(
        self,
        ticker: str,
        market_question: str,
        side: str,
        action: str,
        count: int,
        price_cents: int,
        fair_value: float,
        edge: float,
        status: str = 'open',
        pnl: Optional[float] = None,
        order_id: Optional[str] = None,
    ):
        """
        Log a trade.

        Args:
            ticker: Kalshi market ticker
            market_question: Human-readable market question
            side: 'yes' or 'no'
            action: 'buy' or 'sell'
            count: Number of contracts
            price_cents: Execution price in cents
            fair_value: Estimated fair value (0.0-1.0)
            edge: Edge/mispricing (0.0-1.0)
            status: 'open', 'closed', or 'dry-run'
            pnl: P&L if closed
            order_id: Kalshi order ID
        """
        trade_data = {
            'ticker': ticker,
            'market_question': market_question,
            'side': side,
            'action': action,
            'count': count,
            'price_cents': price_cents,
            'price_probability': price_cents / 100.0,
            'fair_value': fair_value,
            'edge': edge,
            'status': status,
            'pnl': pnl,
            'order_id': order_id,
        }
        self._append_jsonl(self.trades_log, trade_data)

    def log_scan_result(
        self,
        dry_run: bool,
        markets_scanned: int,
        candidates_analyzed: int,
        opportunities_found: int,
        trades_executed: int,
        capital_deployed: float,
        serenbucks_balance: float,
        kalshi_balance: float,
        errors: Optional[list] = None,
    ):
        """
        Log scan cycle results.

        Args:
            dry_run: Whether this was a dry-run
            markets_scanned: Number of markets scanned
            candidates_analyzed: Number analyzed with Perplexity/Claude
            opportunities_found: Number with edge > threshold
            trades_executed: Number of trades placed
            capital_deployed: Total capital deployed this cycle
            serenbucks_balance: Remaining SerenBucks
            kalshi_balance: Kalshi USD balance
            errors: List of errors encountered
        """
        scan_data = {
            'dry_run': dry_run,
            'markets_scanned': markets_scanned,
            'candidates_analyzed': candidates_analyzed,
            'opportunities_found': opportunities_found,
            'trades_executed': trades_executed,
            'capital_deployed': capital_deployed,
            'serenbucks_balance': serenbucks_balance,
            'kalshi_balance': kalshi_balance,
            'errors': errors or [],
        }
        self._append_jsonl(self.scans_log, scan_data)

    def log_notification(
        self,
        level: str,
        title: str,
        message: str,
        data: Optional[Dict] = None,
    ):
        """
        Log a notification.

        Args:
            level: 'info', 'warning', or 'error'
            title: Notification title
            message: Notification message
            data: Additional data
        """
        notification = {
            'level': level,
            'title': title,
            'message': message,
            'data': data or {},
        }
        self._append_jsonl(self.notifications_log, notification)

    def notify_trade_executed(
        self,
        ticker: str,
        side: str,
        count: int,
        price_cents: int,
        edge: float,
        position_size: float,
    ):
        """Log a trade execution notification"""
        self.log_notification(
            level='info',
            title='Trade Executed',
            message=(
                f"Placed {side.upper()} order on {ticker}\n"
                f"  Contracts: {count}\n"
                f"  Price: {price_cents}c (${price_cents / 100:.2f})\n"
                f"  Edge: {edge * 100:.1f}%\n"
                f"  Position size: ${position_size:.2f}"
            ),
            data={
                'ticker': ticker,
                'side': side,
                'count': count,
                'price_cents': price_cents,
                'edge': edge,
            },
        )

    def notify_low_balance(
        self,
        balance_type: str,
        current: float,
        recommended: float,
    ):
        """Log low balance notification"""
        self.log_notification(
            level='warning',
            title=f'Low {balance_type.title()} Balance',
            message=(
                f"Low {balance_type} balance:\n"
                f"  Current: ${current:.2f}\n"
                f"  Recommended: ${recommended:.2f}"
            ),
            data={
                'balance_type': balance_type,
                'current': current,
                'recommended': recommended,
            },
        )

    def notify_api_error(self, error: str, will_retry: bool = True):
        """Log API error notification"""
        status = "Will retry automatically" if will_retry else "Manual intervention required"
        self.log_notification(
            level='warning',
            title='API Error',
            message=f"Error: {error}\nStatus: {status}",
            data={'error': error, 'will_retry': will_retry},
        )
