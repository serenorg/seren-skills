"""
Position Tracker - Manages open positions and P&L calculation.

Tracks:
- Open positions with entry prices
- Unrealized P&L
- Position updates
- Current bankroll calculation
"""

import json
import math
import os
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone


try:
    from serendb_storage import SerenDBStorage
    SERENDB_AVAILABLE = True
except ImportError:
    SERENDB_AVAILABLE = False


class Position:
    """Represents a single position"""

    def __init__(
        self,
        market: str,
        market_id: str,
        token_id: str,
        side: str,
        entry_price: float,
        size: float,
        opened_at: str,
        quantity: Optional[float] = None,
        thesis_side: Optional[str] = None,
        event_id: str = "",
        end_date: str = "",
    ):
        self.market = market
        self.market_id = market_id
        self.token_id = token_id
        self.side = self._normalize_token_side(side)  # 'YES' or 'NO'
        self.thesis_side = self._normalize_thesis_side(thesis_side or side, self.side)
        self.entry_price = entry_price
        self.size = size
        self.opened_at = opened_at
        self.quantity = self._normalize_quantity(quantity)
        if self.quantity <= 0 and self.entry_price > 0 and self.size > 0:
            self.quantity = self.size / self.entry_price
        self.current_price = entry_price  # Will be updated
        self.unrealized_pnl = 0.0
        self.event_id = event_id
        self.end_date = end_date

    def update_price(self, current_price: float):
        """Update current price and calculate unrealized P&L"""
        self.current_price = current_price
        if self.quantity > 0:
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        else:
            self.unrealized_pnl = 0.0

    def update_snapshot(
        self,
        *,
        market: Optional[str] = None,
        token_id: Optional[str] = None,
        entry_price: Optional[float] = None,
        current_price: Optional[float] = None,
        quantity: Optional[float] = None,
        size: Optional[float] = None,
        side: Optional[str] = None,
        thesis_side: Optional[str] = None,
        event_id: Optional[str] = None,
        end_date: Optional[str] = None,
        opened_at: Optional[str] = None,
    ) -> None:
        """Refresh a position snapshot from live data."""
        if market:
            self.market = market
        if token_id:
            self.token_id = token_id
        if side:
            self.side = self._normalize_token_side(side)
        if thesis_side:
            self.thesis_side = self._normalize_thesis_side(thesis_side, self.side)
        if event_id is not None:
            self.event_id = event_id
        if end_date is not None:
            self.end_date = end_date
        if opened_at:
            self.opened_at = opened_at
        if quantity is not None:
            self.quantity = self._normalize_quantity(quantity)
        if entry_price is not None and entry_price > 0:
            self.entry_price = entry_price
        if size is not None and size > 0:
            self.size = size
        if self.quantity <= 0 and self.entry_price > 0 and self.size > 0:
            self.quantity = self.size / self.entry_price
        if current_price is not None and current_price > 0:
            self.update_price(current_price)

    @staticmethod
    def _normalize_token_side(raw_side: str) -> str:
        value = str(raw_side or "").strip().upper()
        if value in {"NO", "SELL", "SHORT"}:
            return "NO"
        return "YES"

    @staticmethod
    def _normalize_thesis_side(raw_side: str, token_side: str) -> str:
        value = str(raw_side or "").strip().upper()
        if value in {"BUY", "SELL"}:
            return value
        return "BUY" if token_side == "YES" else "SELL"

    @staticmethod
    def _normalize_quantity(raw_quantity: Optional[float]) -> float:
        if raw_quantity is None:
            return 0.0
        try:
            quantity = float(raw_quantity)
        except (TypeError, ValueError):
            return 0.0
        if math.isnan(quantity) or quantity < 0:
            return 0.0
        return quantity

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return {
            'market': self.market,
            'market_id': self.market_id,
            'token_id': self.token_id,
            'side': self.side,
            'thesis_side': self.thesis_side,
            'entry_price': self.entry_price,
            'current_price': self.current_price,
            'size': self.size,
            'quantity': self.quantity,
            'unrealized_pnl': round(self.unrealized_pnl, 2),
            'opened_at': self.opened_at,
            'event_id': self.event_id,
            'end_date': self.end_date,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'Position':
        """Create Position from dictionary"""
        pos = cls(
            market=data['market'],
            market_id=data['market_id'],
            token_id=data['token_id'],
            side=data['side'],
            entry_price=data['entry_price'],
            size=data['size'],
            opened_at=data['opened_at'],
            quantity=data.get('quantity'),
            thesis_side=data.get('thesis_side'),
            event_id=data.get('event_id', ''),
            end_date=data.get('end_date', ''),
        )
        pos.current_price = data.get('current_price', data['entry_price'])
        pos.unrealized_pnl = data.get('unrealized_pnl', 0.0)
        return pos


class PositionTracker:
    """Tracks all open positions and P&L"""

    def __init__(
        self,
        positions_file: str = 'logs/positions.json',
        serendb_storage: Optional['SerenDBStorage'] = None,
        use_serendb: bool = True
    ):
        """
        Initialize position tracker

        Args:
            positions_file: Legacy file path for JSON storage
            serendb_storage: SerenDB storage instance (if None, uses file storage)
            use_serendb: Whether to prefer SerenDB over file storage
        """
        self.positions_file = positions_file
        self.serendb = serendb_storage if use_serendb and SERENDB_AVAILABLE else None
        self.positions: Dict[str, Position] = {}
        self.load()

    def load(self):
        """Load positions from SerenDB or file"""
        if self.serendb:
            # Load from SerenDB
            try:
                db_positions = self.serendb.get_positions()
                self.positions = {}
                for pos_data in db_positions:
                    pos = Position.from_dict(pos_data)
                    self.positions[pos.market_id] = pos
            except Exception as e:
                print(f"Error loading positions from SerenDB: {e}")
                self.positions = {}
        else:
            # Legacy file-based loading
            if not os.path.exists(self.positions_file):
                self.positions = {}
                return

            try:
                with open(self.positions_file, 'r') as f:
                    data = json.load(f)

                self.positions = {}
                for pos_data in data.get('positions', []):
                    pos = Position.from_dict(pos_data)
                    self.positions[pos.market_id] = pos

            except Exception as e:
                print(f"Error loading positions from file: {e}")
                self.positions = {}

    def save(self):
        """Save positions to SerenDB or file"""
        if self.serendb:
            # Save to SerenDB
            try:
                for pos in self.positions.values():
                    self.serendb.save_position(pos.to_dict())
            except Exception as e:
                print(f"Error saving positions to SerenDB: {e}")
        else:
            # Legacy file-based saving
            os.makedirs(os.path.dirname(self.positions_file), exist_ok=True)

            data = {
                'positions': [pos.to_dict() for pos in self.positions.values()],
                'total_unrealized_pnl': self.get_total_unrealized_pnl(),
                'position_count': len(self.positions),
                'last_updated': datetime.now(timezone.utc).isoformat()
            }

            with open(self.positions_file, 'w') as f:
                json.dump(data, f, indent=2)

    def add_position(
        self,
        market: str,
        market_id: str,
        token_id: str,
        side: str,
        entry_price: float,
        size: float,
        *,
        quantity: Optional[float] = None,
        thesis_side: Optional[str] = None,
        event_id: str = "",
        end_date: str = "",
    ) -> Position:
        """
        Add a new position

        Args:
            market: Market question/name
            market_id: Market ID
            token_id: Token ID
            side: 'YES' or 'NO' token being held
            entry_price: Entry price (0.0-1.0)
            size: Position size in USDC

        Returns:
            Created Position object
        """
        pos = Position(
            market=market,
            market_id=market_id,
            token_id=token_id,
            side=side,
            entry_price=entry_price,
            size=size,
            opened_at=datetime.now(timezone.utc).isoformat(),
            quantity=quantity,
            thesis_side=thesis_side,
            event_id=event_id,
            end_date=end_date,
        )

        self.positions[market_id] = pos
        self.save()
        return pos

    def remove_position(self, market_id: str) -> Optional[Position]:
        """Remove a position"""
        pos = self.positions.pop(market_id, None)
        if pos:
            self.save()
        return pos

    def update_prices(self, prices: Dict[str, float]):
        """
        Update current prices for positions

        Args:
            prices: Dict mapping market_id -> current_price
        """
        for market_id, price in prices.items():
            if market_id in self.positions:
                self.positions[market_id].update_price(price)

        self.save()

    def get_position(self, market_id: str) -> Optional[Position]:
        """Get a specific position"""
        return self.positions.get(market_id)

    def get_all_positions(self) -> List[Position]:
        """Get all positions"""
        return list(self.positions.values())

    def get_total_unrealized_pnl(self) -> float:
        """Calculate total unrealized P&L across all positions"""
        return sum(pos.unrealized_pnl for pos in self.positions.values())

    def get_total_deployed(self) -> float:
        """Calculate total capital deployed in positions"""
        return sum(pos.size for pos in self.positions.values())

    def get_current_bankroll(self, initial_bankroll: float) -> float:
        """
        Calculate current bankroll

        Args:
            initial_bankroll: Starting bankroll

        Returns:
            Current bankroll (initial + unrealized P&L)
        """
        return initial_bankroll + self.get_total_unrealized_pnl()

    def get_available_capital(self, initial_bankroll: float) -> float:
        """
        Calculate available capital (not deployed)

        Args:
            initial_bankroll: Starting bankroll

        Returns:
            Available capital
        """
        current = self.get_current_bankroll(initial_bankroll)
        deployed = self.get_total_deployed()
        return current - deployed

    def has_position(self, market_id: str) -> bool:
        """Check if we have a position in this market"""
        return market_id in self.positions

    def has_exposure(
        self,
        market_id: str,
        *,
        token_id: str = "",
        no_token_id: str = "",
    ) -> bool:
        """Check for exposure by market id or token ids."""
        if market_id in self.positions:
            return True
        candidate_tokens = {token_id, no_token_id}
        candidate_tokens.discard("")
        if not candidate_tokens:
            return False
        return any(position.token_id in candidate_tokens for position in self.positions.values())

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(parsed):
            return None
        return parsed

    @classmethod
    def _first_numeric(cls, *values: Any) -> Optional[float]:
        for value in values:
            parsed = cls._safe_float(value)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _first_text(*values: Any) -> str:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    @classmethod
    def _infer_quantity(cls, api_pos: Dict[str, Any]) -> float:
        for key in (
            'size',
            'amount',
            'quantity',
            'position',
            'balance',
            'shares',
            'outcomeTokens',
            'token_balance',
        ):
            parsed = cls._safe_float(api_pos.get(key))
            if parsed is not None and parsed > 0:
                return parsed
        nested = api_pos.get('available')
        if isinstance(nested, dict):
            for key in ('amount', 'balance', 'position', 'shares'):
                parsed = cls._safe_float(nested.get(key))
                if parsed is not None and parsed > 0:
                    return parsed
        return 0.0

    @classmethod
    def _infer_entry_price(cls, api_pos: Dict[str, Any], quantity: float) -> Optional[float]:
        direct = cls._first_numeric(
            api_pos.get('entry_price'),
            api_pos.get('avgPrice'),
            api_pos.get('avg_price'),
            api_pos.get('average_price'),
            api_pos.get('price_paid'),
        )
        if direct is not None and direct > 0:
            return direct

        if quantity > 0:
            initial_value = cls._first_numeric(
                api_pos.get('initialValue'),
                api_pos.get('initial_value'),
                api_pos.get('cost_basis'),
                api_pos.get('notional'),
                api_pos.get('size_usd'),
            )
            if initial_value is not None and initial_value > 0:
                return initial_value / quantity
        return None

    @classmethod
    def _infer_current_price(
        cls,
        api_pos: Dict[str, Any],
        quantity: float,
        polymarket_client: Any,
        token_id: str,
    ) -> Optional[float]:
        direct = cls._first_numeric(
            api_pos.get('current_price'),
            api_pos.get('currentPrice'),
            api_pos.get('curPrice'),
            api_pos.get('mark_price'),
            api_pos.get('price'),
        )
        if direct is not None and direct > 0:
            return direct

        if quantity > 0:
            current_value = cls._first_numeric(
                api_pos.get('currentValue'),
                api_pos.get('current_value'),
                api_pos.get('value'),
                api_pos.get('cashValue'),
            )
            if current_value is not None and current_value > 0:
                return current_value / quantity

        if token_id:
            try:
                live_mid = polymarket_client.get_midpoint(token_id)
            except Exception:
                live_mid = None
            if live_mid and live_mid > 0:
                return live_mid
        return None

    @classmethod
    def _infer_position_side(cls, api_pos: Dict[str, Any], token_id: str = "") -> str:
        outcome_text = cls._first_text(
            api_pos.get('outcome'),
            api_pos.get('position_side'),
            api_pos.get('token_side'),
            api_pos.get('title'),
            api_pos.get('side'),
        ).lower()
        if ' no' in f" {outcome_text}" or outcome_text == 'no':
            return 'NO'
        if ' yes' in f" {outcome_text}" or outcome_text == 'yes':
            return 'YES'
        if str(api_pos.get('side', '')).upper() == 'SELL':
            return 'NO'
        if token_id and token_id == str(api_pos.get('no_asset_id', '')).strip():
            return 'NO'
        return 'YES'

    def sync_with_polymarket(self, polymarket_client) -> Dict[str, int]:
        """
        Sync positions with Polymarket API

        Args:
            polymarket_client: PolymarketClient instance

        Returns:
            Dict with 'added', 'removed', 'updated' counts
        """
        try:
            # Get current positions from Polymarket API
            api_positions = polymarket_client.get_positions()
            if isinstance(api_positions, dict):
                api_positions = api_positions.get('data', [])
            if not isinstance(api_positions, list):
                api_positions = []

            # Track changes
            added = 0
            removed = 0
            updated = 0

            # Build set of market_ids from API
            api_market_ids = set()

            # Process each API position
            for api_pos in api_positions:
                # Extract market info (handle different possible formats)
                market_id = self._first_text(
                    api_pos.get('market_id'),
                    api_pos.get('conditionId'),
                    api_pos.get('market'),
                    api_pos.get('condition_id'),
                    api_pos.get('market_slug'),
                    api_pos.get('asset_id'),
                    api_pos.get('token_id'),
                )
                if not market_id:
                    continue

                api_market_ids.add(market_id)
                token_id = self._first_text(
                    api_pos.get('token_id'),
                    api_pos.get('asset_id'),
                    api_pos.get('assetId'),
                    api_pos.get('market'),
                    market_id,
                )
                quantity = self._infer_quantity(api_pos)
                current_price = self._infer_current_price(api_pos, quantity, polymarket_client, token_id)
                entry_price = self._infer_entry_price(api_pos, quantity)
                position_side = self._infer_position_side(api_pos, token_id=token_id)
                thesis_side = self._first_text(api_pos.get('thesis_side'), api_pos.get('side'))
                market_name = self._first_text(
                    api_pos.get('question'),
                    api_pos.get('market_name'),
                    api_pos.get('title'),
                    api_pos.get('market'),
                    market_id,
                )
                event_id = self._first_text(
                    api_pos.get('event_id'),
                    api_pos.get('seriesSlug'),
                    api_pos.get('category'),
                )
                end_date = self._first_text(
                    api_pos.get('end_date'),
                    api_pos.get('endDate'),
                    api_pos.get('endDateIso'),
                    api_pos.get('end_date_iso'),
                )
                notional_size = self._first_numeric(
                    api_pos.get('size_usd'),
                    api_pos.get('notional'),
                    api_pos.get('initialValue'),
                    api_pos.get('initial_value'),
                )
                if notional_size is None and entry_price is not None and quantity > 0:
                    notional_size = entry_price * quantity
                if notional_size is None and current_price is not None and quantity > 0:
                    notional_size = current_price * quantity
                if notional_size is None:
                    notional_size = 0.0

                # Check if we already track this position
                if market_id in self.positions:
                    self.positions[market_id].update_snapshot(
                        market=market_name,
                        token_id=token_id,
                        entry_price=entry_price,
                        current_price=current_price,
                        quantity=quantity,
                        size=notional_size,
                        side=position_side,
                        thesis_side=thesis_side,
                        event_id=event_id,
                        end_date=end_date,
                        opened_at=self._first_text(api_pos.get('created_at'), api_pos.get('opened_at')),
                    )
                    updated += 1
                else:
                    # Add new position from API
                    try:
                        pos = Position(
                            market=market_name,
                            market_id=market_id,
                            token_id=token_id,
                            side=position_side,
                            entry_price=entry_price or current_price or 0.0,
                            size=notional_size,
                            opened_at=self._first_text(api_pos.get('created_at'), api_pos.get('opened_at'))
                            or datetime.now(timezone.utc).isoformat(),
                            quantity=quantity,
                            thesis_side=thesis_side,
                            event_id=event_id,
                            end_date=end_date,
                        )

                        if current_price is not None and current_price > 0:
                            pos.update_price(current_price)

                        self.positions[market_id] = pos
                        added += 1
                    except Exception as e:
                        print(f"Warning: Could not add position {market_id}: {e}")
                        continue

            # Remove positions that no longer exist in API
            local_market_ids = set(self.positions.keys())
            closed_market_ids = local_market_ids - api_market_ids

            for market_id in closed_market_ids:
                del self.positions[market_id]
                removed += 1

            # Save updated positions
            if added > 0 or removed > 0 or updated > 0:
                self.save()

            return {
                'added': added,
                'removed': removed,
                'updated': updated
            }

        except Exception as e:
            print(f"Error syncing positions with Polymarket: {e}")
            return {
                'added': 0,
                'removed': 0,
                'updated': 0
            }
