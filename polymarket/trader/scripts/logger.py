"""
Trading Logger - Logs all trading activity

Maintains three log files:
1. trades.jsonl - One line per trade (opened/closed)
2. scan_results.jsonl - One line per scan cycle
3. notifications.jsonl - Critical events for user notification
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, Optional


try:
    from serendb_storage import SerenDBStorage
    SERENDB_AVAILABLE = True
except ImportError:
    SERENDB_AVAILABLE = False


class TradingLogger:
    """Logs all trading activity to SerenDB or JSONL files"""

    def __init__(
        self,
        trades_log: str = 'logs/trades.jsonl',
        scans_log: str = 'logs/scan_results.jsonl',
        notifications_log: str = 'logs/notifications.jsonl',
        serendb_storage: Optional['SerenDBStorage'] = None,
        use_serendb: bool = True
    ):
        """
        Initialize trading logger

        Args:
            trades_log: Legacy trades log file path
            scans_log: Legacy scans log file path
            notifications_log: Legacy notifications log file path
            serendb_storage: SerenDB storage instance (if None, uses file storage)
            use_serendb: Whether to prefer SerenDB over file storage
        """
        self.trades_log = trades_log
        self.scans_log = scans_log
        self.notifications_log = notifications_log
        self.serendb = serendb_storage if use_serendb and SERENDB_AVAILABLE else None

        # Ensure log directory exists (for legacy file mode)
        if not self.serendb:
            for log_file in [trades_log, scans_log, notifications_log]:
                os.makedirs(os.path.dirname(log_file), exist_ok=True)

    def _append_jsonl(self, filepath: str, data: Dict[str, Any]):
        """Append a JSON line to a file (legacy mode only)"""
        # Add timestamp if not present
        if 'timestamp' not in data:
            data['timestamp'] = datetime.utcnow().isoformat() + 'Z'

        with open(filepath, 'a') as f:
            f.write(json.dumps(data) + '\n')

    def log_trade(
        self,
        market: str,
        market_id: str,
        side: str,
        size: float,
        price: float,
        fair_value: float,
        edge: float,
        status: str = 'open',
        pnl: Optional[float] = None
    ):
        """
        Log a trade

        Args:
            market: Market question
            market_id: Market ID
            side: 'BUY' or 'SELL'
            size: Position size in USDC
            price: Execution price (0.0-1.0)
            fair_value: Estimated fair value
            edge: Edge/mispricing
            status: 'open' or 'closed'
            pnl: P&L if closed
        """
        trade_data = {
            'market': market,
            'market_id': market_id,
            'side': side,
            'size': size,
            'price': price,
            'fair_value': fair_value,
            'edge': edge,
            'status': status,
            'pnl': pnl
        }

        if self.serendb:
            # Save to SerenDB
            try:
                self.serendb.save_trade({
                    'market_id': market_id,
                    'market': market,
                    'side': side,
                    'price': price,
                    'size': size,
                    'executed_at': datetime.utcnow().isoformat() + 'Z',
                    'tx_hash': None
                })
            except Exception as e:
                print(f"Error logging trade to SerenDB: {e}")
        else:
            # Legacy file mode
            self._append_jsonl(self.trades_log, trade_data)

    def log_scan_result(
        self,
        dry_run: bool,
        markets_scanned: int,
        opportunities_found: int,
        trades_executed: int,
        capital_deployed: float,
        api_cost: float,
        serenbucks_balance: float,
        polymarket_balance: float,
        errors: Optional[list] = None
    ):
        """
        Log scan cycle results

        Args:
            dry_run: Whether this was a dry-run
            markets_scanned: Number of markets scanned
            opportunities_found: Number of opportunities identified
            trades_executed: Number of trades placed
            capital_deployed: Total capital deployed
            api_cost: SerenBucks spent on API calls
            serenbucks_balance: Remaining SerenBucks
            polymarket_balance: Polymarket balance
            errors: List of errors encountered
        """
        scan_data = {
            'dry_run': dry_run,
            'markets_scanned': markets_scanned,
            'opportunities_found': opportunities_found,
            'trades_executed': trades_executed,
            'capital_deployed': capital_deployed,
            'api_cost': api_cost,
            'serenbucks_balance': serenbucks_balance,
            'polymarket_balance': polymarket_balance,
            'errors': errors or []
        }

        if self.serendb:
            # Save to SerenDB
            try:
                self.serendb.save_scan_log({
                    'scan_at': datetime.utcnow().isoformat() + 'Z',
                    'markets_scanned': markets_scanned,
                    'opportunities_found': opportunities_found,
                    'trades_executed': trades_executed,
                    'capital_deployed': capital_deployed,
                    'api_cost': api_cost,
                    'serenbucks_balance': serenbucks_balance,
                    'polymarket_balance': polymarket_balance
                })
            except Exception as e:
                print(f"Error logging scan to SerenDB: {e}")
        else:
            # Legacy file mode
            self._append_jsonl(self.scans_log, scan_data)

    def log_notification(
        self,
        level: str,
        title: str,
        message: str,
        data: Optional[Dict] = None
    ):
        """
        Log a notification for the user

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
            'data': data or {}
        }

        self._append_jsonl(self.notifications_log, notification)

    def notify_large_win(
        self,
        market: str,
        entry: float,
        exit: float,
        profit: float,
        roi: float,
        session_pnl: float,
        bankroll: float,
        win_rate: float
    ):
        """Log a large win notification"""
        message = f"""Position closed with profit:
  • Market: "{market}"
  • Entry: ${entry:.2f}
  • Exit: ${exit:.2f}
  • Profit: +${profit:.2f} (+{roi:.1f}%)

Current status:
  • Session P&L: ${session_pnl:+.2f}
  • Bankroll: ${bankroll:.2f}
  • Win rate: {win_rate * 100:.0f}%"""

        self.log_notification(
            level='info',
            title='Significant Win!',
            message=message,
            data={
                'market': market,
                'profit': profit,
                'roi': roi,
                'session_pnl': session_pnl
            }
        )

    def notify_large_loss(
        self,
        market: str,
        entry: float,
        exit: float,
        loss: float,
        roi: float,
        session_pnl: float,
        bankroll: float,
        win_rate: float
    ):
        """Log a large loss notification"""
        message = f"""Significant loss on position:
  • Market: "{market}"
  • Entry: ${entry:.2f}
  • Exit: ${exit:.2f}
  • Loss: ${loss:.2f} ({roi:.1f}%)

Current status:
  • Session P&L: ${session_pnl:+.2f}
  • Bankroll: ${bankroll:.2f}
  • Win rate: {win_rate * 100:.0f}%"""

        self.log_notification(
            level='warning',
            title='Position Closed - Loss',
            message=message,
            data={
                'market': market,
                'loss': loss,
                'roi': roi,
                'session_pnl': session_pnl
            }
        )

    def notify_bankroll_depleted(
        self,
        current: float,
        stop_loss: float,
        open_positions: int,
        unrealized_pnl: float
    ):
        """Log bankroll depletion notification"""
        message = f"""Your bankroll has dropped below the stop loss threshold:
  • Current bankroll: ${current:.2f}
  • Stop loss threshold: ${stop_loss:.2f}
  • Status: Trading paused automatically

Open positions: {open_positions}
Unrealized P&L: ${unrealized_pnl:+.2f}"""

        self.log_notification(
            level='error',
            title='Bankroll Depleted',
            message=message,
            data={
                'current_bankroll': current,
                'stop_loss': stop_loss,
                'open_positions': open_positions
            }
        )

    def notify_api_error(self, error: str, will_retry: bool = True):
        """Log API error notification"""
        status = "Will retry automatically" if will_retry else "Manual intervention required"
        message = f"""Scan cycle failed:
  • Error: {error}
  • Status: {status}"""

        self.log_notification(
            level='warning',
            title='API Error',
            message=message,
            data={'error': error, 'will_retry': will_retry}
        )

    def notify_low_balance(
        self,
        balance_type: str,  # 'serenbucks' or 'polymarket'
        current: float,
        recommended: float
    ):
        """Log low balance notification"""
        message = f"""Low {balance_type} balance:
  • Current: ${current:.2f}
  • Recommended: ${recommended:.2f}"""

        self.log_notification(
            level='warning',
            title=f'Low {balance_type.title()} Balance',
            message=message,
            data={
                'balance_type': balance_type,
                'current': current,
                'recommended': recommended
            }
        )
