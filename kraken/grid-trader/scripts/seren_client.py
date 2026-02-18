"""
Seren Gateway API Client - Routes Kraken calls through Seren Gateway

All Kraken API calls go through api.serendb.com/publishers/kraken
"""

import requests
from typing import Dict, Any, Optional, List


class SerenClient:
    """Wrapper for Seren Gateway API"""

    def __init__(self, api_key: str, base_url: str = 'https://api.serendb.com'):
        """
        Initialize Seren client

        Args:
            api_key: Seren API key (starts with 'sb_')
            base_url: Gateway base URL
        """
        self.api_key = api_key
        self.base_url = base_url

    def _call_publisher(
        self,
        publisher: str,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generic Gateway API caller with error handling

        Args:
            publisher: Publisher name (e.g., 'kraken')
            method: HTTP method ('GET', 'POST', 'DELETE')
            path: API path (e.g., '/0/private/AddOrder')
            body: Request body (for POST)
            params: Query parameters (for GET)

        Returns:
            API response as dict

        Raises:
            requests.HTTPError: On API errors
        """
        url = f"{self.base_url}/publishers/{publisher}{path}"
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=body,
            params=params,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()

        # Unwrap Seren Gateway envelope - response is wrapped in 'body' field
        if isinstance(data, dict) and 'body' in data:
            return data['body']
        return data

    # ========== Kraken Market Data ==========

    def get_ticker(self, pair: str) -> Dict[str, Any]:
        """
        Get ticker information

        Args:
            pair: Trading pair (e.g., 'XBTUSD')

        Returns:
            Ticker data with current price, volume, etc.
        """
        return self._call_publisher(
            publisher='kraken-spot-trading',
            method='GET',
            path='/public/Ticker',
            params={'pair': pair}
        )

    def get_current_price(self, pair: str) -> float:
        """
        Get current price for a trading pair, handling Kraken's pair alias mismatch.

        Kraken returns pair names with aliases (e.g., 'XXBTZUSD' instead of 'XBTUSD').
        This method handles that transparently.

        Args:
            pair: Trading pair (e.g., 'XBTUSD')

        Returns:
            Current price as float

        Raises:
            KeyError: If ticker data is invalid or missing
        """
        ticker = self.get_ticker(pair)

        # Kraken returns result dict with aliased pair name as key
        # Example: request 'XBTUSD', get back {'result': {'XXBTZUSD': {...}}}
        result = ticker.get('result', {})

        # Try exact match first
        if pair in result:
            return float(result[pair]['c'][0])

        # Fall back to first pair in result (handles alias mismatch)
        if result:
            first_pair_key = list(result.keys())[0]
            return float(result[first_pair_key]['c'][0])

        raise KeyError(f"No ticker data found for pair {pair}")

    def get_asset_pairs(self, pair: str) -> Dict[str, Any]:
        """
        Get asset pair information

        Args:
            pair: Trading pair (e.g., 'XBTUSD')

        Returns:
            Pair info (fees, min order size, etc.)
        """
        return self._call_publisher(
            publisher='kraken-spot-trading',
            method='GET',
            path='/public/AssetPairs',
            params={'pair': pair}
        )

    # ========== Kraken Account Data ==========

    def get_balance(self) -> Dict[str, Any]:
        """
        Get account balance

        Returns:
            Dict of asset balances (e.g., {'XXBT': 0.5, 'ZUSD': 1000})
        """
        return self._call_publisher(
            publisher='kraken-spot-trading',
            method='POST',
            path='/private/Balance',
            body={}
        )

    def get_open_orders(self) -> Dict[str, Any]:
        """
        Get open orders

        Returns:
            Dict of open orders by order ID
        """
        return self._call_publisher(
            publisher='kraken-spot-trading',
            method='POST',
            path='/private/OpenOrders',
            body={}
        )

    def get_trade_balance(self, asset: str = 'ZUSD') -> Dict[str, Any]:
        """
        Get trade balance (includes margin info)

        Args:
            asset: Base asset for balance (default: ZUSD)

        Returns:
            Trade balance info
        """
        return self._call_publisher(
            publisher='kraken-spot-trading',
            method='POST',
            path='/private/TradeBalance',
            body={'asset': asset}
        )

    # ========== Kraken Trading ==========

    def add_order(
        self,
        pair: str,
        order_type: str,
        side: str,
        volume: float,
        price: Optional[float] = None,
        validate: bool = False
    ) -> Dict[str, Any]:
        """
        Place a new order

        Args:
            pair: Trading pair (e.g., 'XBTUSD')
            order_type: 'limit' or 'market'
            side: 'buy' or 'sell'
            volume: Order volume (BTC)
            price: Limit price (required for limit orders)
            validate: If True, validate only (don't place order)

        Returns:
            Order response with order ID
        """
        body = {
            'pair': pair,
            'type': side,
            'ordertype': order_type,
            'volume': str(volume)
        }

        if order_type == 'limit' and price is not None:
            body['price'] = str(price)

        if validate:
            body['validate'] = 'true'

        return self._call_publisher(
            publisher='kraken-spot-trading',
            method='POST',
            path='/private/AddOrder',
            body=body
        )

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """
        Cancel an open order

        Args:
            order_id: Order ID to cancel

        Returns:
            Cancellation response
        """
        return self._call_publisher(
            publisher='kraken-spot-trading',
            method='POST',
            path='/private/CancelOrder',
            body={'txid': order_id}
        )

    def cancel_all_orders(self) -> Dict[str, Any]:
        """
        Cancel all open orders

        Returns:
            Cancellation response with count
        """
        return self._call_publisher(
            publisher='kraken-spot-trading',
            method='POST',
            path='/private/CancelAll',
            body={}
        )

    # ========== Kraken History ==========

    def get_closed_orders(
        self,
        trades: bool = True,
        start: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get closed orders

        Args:
            trades: Include trade info
            start: Starting timestamp

        Returns:
            Dict of closed orders
        """
        body = {'trades': trades}
        if start is not None:
            body['start'] = start

        return self._call_publisher(
            publisher='kraken-spot-trading',
            method='POST',
            path='/private/ClosedOrders',
            body=body
        )

    def get_trades_history(
        self,
        pair: Optional[str] = None,
        start: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Get trades history

        Args:
            pair: Filter by trading pair
            start: Starting timestamp

        Returns:
            Dict of trades
        """
        body = {}
        if pair is not None:
            body['pair'] = pair
        if start is not None:
            body['start'] = start

        return self._call_publisher(
            publisher='kraken-spot-trading',
            method='POST',
            path='/private/TradesHistory',
            body=body
        )
