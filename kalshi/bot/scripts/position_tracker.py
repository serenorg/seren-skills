"""
Position Tracker - Manages open Kalshi positions and P&L calculation.

Tracks:
- Open positions with entry prices (in probability, converted from cents)
- Unrealized P&L per position
- Position lifecycle (open, update, close)
- Total portfolio metrics

Kalshi-specific:
- Ticker-based position tracking (not token_id like Polymarket)
- Side is 'yes' or 'no' (Kalshi native)
- Prices stored as probability (0.01-0.99), converted from cents
"""

import json
import os
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone


class Position:
    """Represents a single Kalshi position"""

    def __init__(
        self,
        ticker: str,
        event_ticker: str,
        side: str,
        action: str,
        entry_price: float,
        count: int,
        opened_at: str,
        market_question: str = '',
        end_date: str = '',
    ):
        """
        Args:
            ticker: Kalshi market ticker
            event_ticker: Parent event ticker
            side: 'yes' or 'no'
            action: 'buy' or 'sell'
            entry_price: Entry price as probability (0.01-0.99)
            count: Number of contracts
            opened_at: ISO timestamp
            market_question: Human-readable market question
            end_date: Market expiration/close date
        """
        self.ticker = ticker
        self.event_ticker = event_ticker
        self.side = side.lower()
        self.action = action.lower()
        self.entry_price = entry_price
        self.count = count
        self.opened_at = opened_at
        self.market_question = market_question
        self.end_date = end_date
        self.current_price = entry_price
        self.unrealized_pnl = 0.0

    def update_price(self, current_price: float):
        """Update current price and recalculate unrealized P&L.

        For BUY YES: profit = (current_price - entry_price) * count
        For BUY NO: profit = ((1-entry_price) - (1-current_price)) * count
                          = (current_price - entry_price) * count (when entry is NO cost)
        Actually: For BUY YES @ p, current = c: PnL = (c - p) * count
                  For BUY NO @ (1-p), current NO price = (1-c): PnL = ((1-c) - (1-p)) * count = (p - c) * count
        """
        self.current_price = current_price
        if self.side == 'yes':
            self.unrealized_pnl = (current_price - self.entry_price) * self.count
        else:
            # For NO positions, we paid (1 - entry_price) per contract
            # Current value is (1 - current_price) per contract
            self.unrealized_pnl = (self.entry_price - current_price) * self.count

    def cost_basis(self) -> float:
        """Total cost in USD for this position."""
        if self.side == 'yes':
            return self.entry_price * self.count
        else:
            return (1.0 - self.entry_price) * self.count

    def current_value(self) -> float:
        """Current value in USD."""
        if self.side == 'yes':
            return self.current_price * self.count
        else:
            return (1.0 - self.current_price) * self.count

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return {
            'ticker': self.ticker,
            'event_ticker': self.event_ticker,
            'side': self.side,
            'action': self.action,
            'entry_price': self.entry_price,
            'current_price': self.current_price,
            'count': self.count,
            'unrealized_pnl': round(self.unrealized_pnl, 4),
            'cost_basis': round(self.cost_basis(), 4),
            'current_value': round(self.current_value(), 4),
            'opened_at': self.opened_at,
            'market_question': self.market_question,
            'end_date': self.end_date,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Position':
        """Create Position from dictionary"""
        pos = cls(
            ticker=data['ticker'],
            event_ticker=data.get('event_ticker', ''),
            side=data['side'],
            action=data.get('action', 'buy'),
            entry_price=data['entry_price'],
            count=data['count'],
            opened_at=data['opened_at'],
            market_question=data.get('market_question', ''),
            end_date=data.get('end_date', ''),
        )
        pos.current_price = data.get('current_price', data['entry_price'])
        pos.unrealized_pnl = data.get('unrealized_pnl', 0.0)
        return pos


class PositionTracker:
    """Tracks all open Kalshi positions and portfolio P&L"""

    def __init__(self, positions_file: str = 'logs/positions.json'):
        """
        Initialize position tracker.

        Args:
            positions_file: Path for JSON position storage
        """
        self.positions_file = positions_file
        self.positions: Dict[str, Position] = {}
        self.load()

    def load(self):
        """Load positions from file"""
        if not os.path.exists(self.positions_file):
            self.positions = {}
            return

        try:
            with open(self.positions_file, 'r') as f:
                data = json.load(f)

            self.positions = {}
            for pos_data in data.get('positions', []):
                pos = Position.from_dict(pos_data)
                self.positions[pos.ticker] = pos
        except Exception as e:
            print(f"Error loading positions: {e}")
            self.positions = {}

    def save(self):
        """Save positions to file"""
        os.makedirs(os.path.dirname(self.positions_file) or '.', exist_ok=True)

        data = {
            'positions': [pos.to_dict() for pos in self.positions.values()],
            'total_unrealized_pnl': round(self.get_total_pnl(), 4),
            'position_count': len(self.positions),
            'total_cost_basis': round(self.get_total_cost_basis(), 4),
            'last_updated': datetime.now(timezone.utc).isoformat(),
        }

        with open(self.positions_file, 'w') as f:
            json.dump(data, f, indent=2)

    def add_position(self, trade_data: Dict[str, Any]) -> Position:
        """
        Add a new position from trade data.

        Args:
            trade_data: Dict with keys: ticker, event_ticker, side, action,
                        entry_price, count, market_question, end_date

        Returns:
            Created Position object
        """
        pos = Position(
            ticker=trade_data['ticker'],
            event_ticker=trade_data.get('event_ticker', ''),
            side=trade_data['side'],
            action=trade_data.get('action', 'buy'),
            entry_price=trade_data['entry_price'],
            count=trade_data['count'],
            opened_at=datetime.now(timezone.utc).isoformat(),
            market_question=trade_data.get('market_question', ''),
            end_date=trade_data.get('end_date', ''),
        )

        # If position already exists, merge
        existing = self.positions.get(pos.ticker)
        if existing and existing.side == pos.side:
            # Average entry price and add contracts
            total_cost = existing.cost_basis() + pos.cost_basis()
            total_count = existing.count + pos.count
            if total_count > 0:
                if pos.side == 'yes':
                    existing.entry_price = total_cost / total_count
                else:
                    existing.entry_price = 1.0 - (total_cost / total_count)
            existing.count = total_count
            self.save()
            return existing

        self.positions[pos.ticker] = pos
        self.save()
        return pos

    def update_prices(self, kalshi_client) -> int:
        """
        Update current prices for all positions from Kalshi API.

        Args:
            kalshi_client: KalshiClient instance

        Returns:
            Number of positions updated
        """
        updated = 0
        for ticker, pos in list(self.positions.items()):
            try:
                market_data = kalshi_client.get_market(ticker)
                market = market_data.get('market', market_data)
                # Use yes_price or last_price
                yes_price_cents = market.get('yes_price') or market.get('last_price', 0)
                if yes_price_cents:
                    current_prob = int(yes_price_cents) / 100.0
                    pos.update_price(current_prob)
                    updated += 1
            except Exception as e:
                print(f"  Warning: Failed to update price for {ticker}: {e}")

        if updated > 0:
            self.save()
        return updated

    def get_positions(self) -> List[Dict]:
        """Get all positions as dicts"""
        return [pos.to_dict() for pos in self.positions.values()]

    def get_position(self, ticker: str) -> Optional[Position]:
        """Get a specific position by ticker"""
        return self.positions.get(ticker)

    def has_position(self, ticker: str) -> bool:
        """Check if we have a position in this market"""
        return ticker in self.positions

    def close_position(self, ticker: str) -> Optional[Position]:
        """Remove a position (after closing trade)"""
        pos = self.positions.pop(ticker, None)
        if pos:
            self.save()
        return pos

    def get_total_pnl(self) -> float:
        """Calculate total unrealized P&L"""
        return sum(pos.unrealized_pnl for pos in self.positions.values())

    def get_total_cost_basis(self) -> float:
        """Calculate total capital deployed"""
        return sum(pos.cost_basis() for pos in self.positions.values())

    def get_total_value(self) -> float:
        """Calculate total current value"""
        return sum(pos.current_value() for pos in self.positions.values())

    def get_event_exposure(self) -> Dict[str, int]:
        """Count positions per event ticker (for diversification)"""
        exposure: Dict[str, int] = {}
        for pos in self.positions.values():
            event = pos.event_ticker or 'unknown'
            exposure[event] = exposure.get(event, 0) + 1
        return exposure
