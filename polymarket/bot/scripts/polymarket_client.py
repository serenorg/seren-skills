"""
Polymarket Client - Wrapper for Polymarket CLOB API

Uses ``py-clob-client`` (via DirectClobTrader from scripts/polymarket_live.py) for
trading operations and the ``polymarket-data`` Seren publisher for market discovery.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from seren_client import SerenClient


class PolymarketClient:
    """Client for Polymarket CLOB API.

    Market discovery uses the ``polymarket-data`` Seren publisher.
    Trading operations use ``DirectClobTrader`` from ``polymarket_live.py``
    (wraps ``py-clob-client`` for local EIP-712 signing).
    """

    def __init__(
        self,
        seren_client: SerenClient,
        dry_run: bool = False,
        **_ignored: Any,
    ):
        """
        Initialize Polymarket client.

        Args:
            seren_client: Seren client instance (used for market data publisher).
            dry_run: When True, skip DirectClobTrader init (no credentials needed).
        """
        self.seren = seren_client
        self._trader: Any = None

        if not dry_run:
            try:
                from polymarket_live import DirectClobTrader

                self._trader = DirectClobTrader(
                    skill_root=Path(__file__).resolve().parents[1],
                    client_name="polymarket-bot",
                )
            except RuntimeError:
                # Credentials missing — trading will fail but market scanning works.
                pass

    # -- Market discovery (polymarket-data publisher) -------------------------

    def get_markets(self, limit: int = 500, active: bool = True) -> List[Dict]:
        """
        Get list of prediction markets via polymarket-data publisher.

        Args:
            limit: Max markets to return
            active: Only active markets

        Returns:
            List of market dicts
        """
        params = f"?limit={limit}&active={'true' if active else 'false'}&closed=false"

        response = self.seren.call_publisher(
            publisher='polymarket-data',
            method='GET',
            path=f'/markets{params}'
        )

        markets: List[Dict] = []

        market_list = response.get('body', [])
        if not market_list and 'data' in response:
            market_list = response.get('data', [])

        for market_data in market_list:
            if market_data.get('closed', False):
                continue

            market_id = market_data.get('conditionId') or market_data.get('id')
            question = market_data.get('question', '')

            clob_token_ids_str = market_data.get('clobTokenIds', '[]')
            try:
                token_ids = json.loads(clob_token_ids_str) if isinstance(clob_token_ids_str, str) else clob_token_ids_str
            except Exception:
                token_ids = []

            if not token_ids:
                continue

            yes_token_id = token_ids[0]
            no_token_id = token_ids[1] if len(token_ids) > 1 else None

            outcome_prices = market_data.get('outcomePrices', ['0.5'])
            try:
                price = float(outcome_prices[0]) if outcome_prices else 0.5
            except Exception:
                price = 0.5

            volume = float(market_data.get('volume', 0))
            liquidity = float(market_data.get('liquidity', 0))
            end_date = market_data.get('endDateIso') or market_data.get('end_date_iso', '')

            if liquidity < 100:
                continue

            markets.append({
                'market_id': market_id,
                'question': question,
                'token_id': yes_token_id,
                'no_token_id': no_token_id,
                'price': price,
                'volume': volume,
                'liquidity': liquidity,
                'end_date': end_date,
            })

        return markets[:limit]

    # -- Trading operations (DirectClobTrader / py-clob-client) ---------------

    def _require_trader(self) -> Any:
        if self._trader is None:
            raise RuntimeError(
                "Trading requires py-clob-client credentials. "
                "Set POLY_PRIVATE_KEY, POLY_API_KEY, POLY_PASSPHRASE, and POLY_SECRET."
            )
        return self._trader

    def place_order(
        self,
        token_id: str,
        side: str,
        size: float,
        price: float,
        order_type: str = 'GTC',
    ) -> Dict:
        """Place an order via py-clob-client (local EIP-712 signing)."""
        del order_type
        from polymarket_live import fetch_book, fetch_fee_rate_bps, snap_price

        trader = self._require_trader()
        book = fetch_book(token_id)
        tick_size = str(book.get("tick_size", "0.01"))
        neg_risk = bool(book.get("neg_risk", False))
        fee_rate_bps = fetch_fee_rate_bps(token_id)
        snapped_price = snap_price(price, tick_size, side)
        return trader.create_order(
            token_id=token_id,
            side=side,
            price=snapped_price,
            size=size,
            tick_size=tick_size,
            neg_risk=neg_risk,
            fee_rate_bps=fee_rate_bps,
        )

    def cancel_order(self, order_id: str) -> Dict:
        """Cancel all open orders."""
        trader = self._require_trader()
        return trader.cancel_all()

    def get_positions(self) -> List[Dict]:
        """Get current positions."""
        trader = self._require_trader()
        result = trader.get_positions()
        return result if isinstance(result, list) else []

    def get_open_orders(self, market: Optional[str] = None) -> List[Dict]:
        """Get open orders."""
        trader = self._require_trader()
        result = trader.get_orders()
        if isinstance(result, list) and market:
            return [o for o in result if o.get('market') == market]
        return result if isinstance(result, list) else []

    def get_balance(self) -> float:
        """Get USDC balance from Polymarket wallet."""
        try:
            trader = self._require_trader()
            return trader.get_cash_balance()
        except Exception:
            return 0.0

    def _get_book_levels(self, token_id: str):
        """Get best bid/ask from CLOB, falling back to raw data if parsed lists are empty."""
        from polymarket_live import fetch_book
        book = fetch_book(token_id)
        bids = book.get('bids', [])
        asks = book.get('asks', [])
        if not bids or not asks:
            raw = book.get('raw', {})
            if isinstance(raw, dict):
                bids = bids or raw.get('bids', [])
                asks = asks or raw.get('asks', [])
        best_bid = float(bids[0]['price']) if bids else 0.0
        best_ask = float(asks[0]['price']) if asks else 0.0
        return best_bid, best_ask

    def get_price(self, token_id: str, side: str) -> float:
        """Get current price for a token from the CLOB orderbook."""
        best_bid, best_ask = self._get_book_levels(token_id)
        if side.upper() == 'BUY':
            return best_ask
        else:
            return best_bid

    def get_midpoint(self, token_id: str) -> float:
        """Get midpoint price (average of best bid and ask)."""
        best_bid, best_ask = self._get_book_levels(token_id)
        if best_bid and best_ask:
            return (best_bid + best_ask) / 2.0
        return best_bid or best_ask
