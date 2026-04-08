"""
Kalshi Client - Direct REST API client for Kalshi prediction markets

Handles RSA-PSS request signing, market data, order management,
positions, and fills. This replaces polymarket_client.py and
polymarket_live.py for the Kalshi trading bot.

Auth: RSA private key signing (KALSHI-ACCESS-KEY, KALSHI-ACCESS-SIGNATURE,
KALSHI-ACCESS-TIMESTAMP headers).

Prices are in CENTS (1-99). Contracts pay $1.00 if correct, $0.00 if wrong.
"""

import base64
import os
import time
from typing import Any, Dict, List, Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, utils


# Production base URL
KALSHI_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

# Demo base URL (for testing)
KALSHI_DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"


class KalshiClient:
    """Direct client for the Kalshi REST API with RSA-PSS auth signing."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        private_key_path: Optional[str] = None,
        private_key_pem: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        """
        Initialize Kalshi REST API client.

        Auth uses RSA-PSS signing. Provide either private_key_path or
        private_key_pem (inline PEM string).

        Args:
            api_key: Kalshi API key (KALSHI-ACCESS-KEY header)
            private_key_path: Path to RSA private key PEM file
            private_key_pem: Inline RSA private key PEM string
            base_url: Override API base URL (default: production)
        """
        self.api_key = api_key or os.getenv('KALSHI_API_KEY', '')
        self.base_url = (base_url or os.getenv('KALSHI_BASE_URL', KALSHI_BASE_URL)).rstrip('/')

        # Load RSA private key
        pem_data = private_key_pem or os.getenv('KALSHI_PRIVATE_KEY', '')
        key_path = private_key_path or os.getenv('KALSHI_PRIVATE_KEY_PATH', '')

        if pem_data:
            self._private_key = serialization.load_pem_private_key(
                pem_data.encode('utf-8'), password=None
            )
        elif key_path and os.path.exists(key_path):
            with open(key_path, 'rb') as f:
                self._private_key = serialization.load_pem_private_key(
                    f.read(), password=None
                )
        else:
            self._private_key = None

        self.session = requests.Session()

    def is_authenticated(self) -> bool:
        """Check if client has credentials for authenticated requests."""
        return bool(self.api_key and self._private_key)

    def _sign_request(
        self,
        method: str,
        path: str,
        body: str = '',
    ) -> Dict[str, str]:
        """
        Generate RSA-PSS auth headers for a Kalshi API request.

        The signature message is: timestamp + method + path + body
        Signed with RSA-PSS (SHA256, max salt length).

        Args:
            method: HTTP method (GET, POST, DELETE)
            path: API path (e.g., /trade-api/v2/markets)
            body: Request body string (empty for GET/DELETE)

        Returns:
            Dict of auth headers
        """
        if not self._private_key:
            raise RuntimeError(
                "Kalshi private key not configured. Set KALSHI_PRIVATE_KEY_PATH "
                "or KALSHI_PRIVATE_KEY env var."
            )

        timestamp_ms = str(int(time.time() * 1000))
        message = timestamp_ms + method.upper() + path + body

        signature = self._private_key.sign(
            message.encode('utf-8'),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            utils.Prehashed(hashes.SHA256())
            if False  # Kalshi uses raw SHA256, not prehashed
            else hashes.SHA256(),
        )

        return {
            'KALSHI-ACCESS-KEY': self.api_key,
            'KALSHI-ACCESS-SIGNATURE': base64.b64encode(signature).decode('utf-8'),
            'KALSHI-ACCESS-TIMESTAMP': timestamp_ms,
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        authenticated: bool = True,
    ) -> Dict[str, Any]:
        """
        Make an HTTP request to the Kalshi API.

        Args:
            method: HTTP method
            endpoint: API endpoint (e.g., '/markets')
            body: JSON body for POST/PUT
            params: Query parameters
            authenticated: Whether to include auth headers

        Returns:
            Response JSON as dict
        """
        url = f"{self.base_url}{endpoint}"
        # Path used for signing includes /trade-api/v2 prefix
        path = endpoint if endpoint.startswith('/trade-api') else f"/trade-api/v2{endpoint}"

        import json as _json
        body_str = _json.dumps(body) if body else ''

        headers = {'Content-Type': 'application/json'}

        if authenticated and self._private_key:
            auth_headers = self._sign_request(method.upper(), path, body_str)
            headers.update(auth_headers)

        kwargs: Dict[str, Any] = {
            'headers': headers,
            'timeout': 30,
        }
        if body:
            kwargs['json'] = body
        if params:
            kwargs['params'] = {k: v for k, v in params.items() if v is not None}

        response = self.session.request(method, url, **kwargs)

        if response.status_code >= 400:
            try:
                error_data = response.json()
                error_msg = error_data.get('message', error_data.get('error', response.text))
            except Exception:
                error_msg = response.text
            raise Exception(
                f"Kalshi API error: {response.status_code} - {error_msg}"
            )

        if response.status_code == 204:
            return {'status': 'ok'}

        try:
            return response.json()
        except Exception:
            return {'text': response.text}

    # ---- Market Data (public) ----

    def get_markets(
        self,
        limit: int = 200,
        cursor: Optional[str] = None,
        status: Optional[str] = 'open',
        event_ticker: Optional[str] = None,
        series_ticker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List markets from Kalshi.

        Args:
            limit: Max markets to return (1-1000)
            cursor: Pagination cursor
            status: Filter by status ('open', 'closed', 'settled')
            event_ticker: Filter by event
            series_ticker: Filter by series

        Returns:
            {'markets': [...], 'cursor': '...'}
        """
        params = {
            'limit': limit,
            'cursor': cursor,
            'status': status,
            'event_ticker': event_ticker,
            'series_ticker': series_ticker,
        }
        return self._request('GET', '/markets', params=params, authenticated=False)

    def get_market(self, ticker: str) -> Dict[str, Any]:
        """
        Get details for a single market.

        Args:
            ticker: Market ticker (e.g., 'KXBTC-25APR08-T105000')

        Returns:
            Market detail dict
        """
        return self._request('GET', f'/markets/{ticker}', authenticated=False)

    def get_orderbook(self, ticker: str) -> Dict[str, Any]:
        """
        Get orderbook for a market.

        Args:
            ticker: Market ticker

        Returns:
            {'yes': [...], 'no': [...]} with price/quantity levels
        """
        return self._request('GET', f'/markets/{ticker}/orderbook', authenticated=False)

    def get_event(self, event_ticker: str) -> Dict[str, Any]:
        """
        Get event details.

        Args:
            event_ticker: Event ticker

        Returns:
            Event detail dict including child markets
        """
        return self._request('GET', f'/events/{event_ticker}', authenticated=False)

    def get_events(
        self,
        limit: int = 100,
        cursor: Optional[str] = None,
        status: Optional[str] = 'open',
    ) -> Dict[str, Any]:
        """
        List events.

        Args:
            limit: Max events to return
            cursor: Pagination cursor
            status: Filter by status

        Returns:
            {'events': [...], 'cursor': '...'}
        """
        params = {'limit': limit, 'cursor': cursor, 'status': status}
        return self._request('GET', '/events', params=params, authenticated=False)

    # ---- Trading (authenticated) ----

    def create_order(
        self,
        ticker: str,
        action: str,
        side: str,
        count: int,
        price_cents: Optional[int] = None,
        order_type: str = 'limit',
        expiration_ts: Optional[int] = None,
        sell_position_floor: Optional[int] = None,
        buy_max_cost: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Place an order on Kalshi.

        Args:
            ticker: Market ticker
            action: 'buy' or 'sell'
            side: 'yes' or 'no'
            count: Number of contracts
            price_cents: Limit price in cents (1-99). Required for limit orders.
            order_type: 'limit' or 'market'
            expiration_ts: Optional expiration timestamp (epoch seconds)
            sell_position_floor: Minimum contracts to keep when selling
            buy_max_cost: Maximum total cost for market buy orders (cents)

        Returns:
            Order response dict with order_id
        """
        body: Dict[str, Any] = {
            'ticker': ticker,
            'action': action.lower(),
            'type': order_type.lower(),
            'side': side.lower(),
            'count': count,
        }

        if order_type.lower() == 'limit' and price_cents is not None:
            if side.lower() == 'yes':
                body['yes_price'] = price_cents
            else:
                body['no_price'] = price_cents

        if expiration_ts is not None:
            body['expiration_ts'] = expiration_ts
        if sell_position_floor is not None:
            body['sell_position_floor'] = sell_position_floor
        if buy_max_cost is not None:
            body['buy_max_cost'] = buy_max_cost

        return self._request('POST', '/portfolio/orders', body=body)

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """
        Cancel an open order.

        Args:
            order_id: Order ID to cancel

        Returns:
            Cancellation response
        """
        return self._request('DELETE', f'/portfolio/orders/{order_id}')

    def get_orders(
        self,
        ticker: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List orders.

        Args:
            ticker: Filter by market ticker
            status: Filter by status ('resting', 'canceled', 'executed', 'pending')

        Returns:
            {'orders': [...]}
        """
        params = {'ticker': ticker, 'status': status}
        return self._request('GET', '/portfolio/orders', params=params)

    # ---- Portfolio (authenticated) ----

    def get_positions(
        self,
        limit: int = 200,
        cursor: Optional[str] = None,
        settlement_status: Optional[str] = None,
        event_ticker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List portfolio positions.

        Args:
            limit: Max positions to return
            cursor: Pagination cursor
            settlement_status: Filter ('unsettled', 'settled')
            event_ticker: Filter by event

        Returns:
            {'market_positions': [...], 'cursor': '...'}
        """
        params = {
            'limit': limit,
            'cursor': cursor,
            'settlement_status': settlement_status,
            'event_ticker': event_ticker,
        }
        return self._request('GET', '/portfolio/positions', params=params)

    def get_fills(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get fill history.

        Args:
            ticker: Filter by market ticker
            limit: Max fills to return
            cursor: Pagination cursor

        Returns:
            {'fills': [...], 'cursor': '...'}
        """
        params = {'ticker': ticker, 'limit': limit, 'cursor': cursor}
        return self._request('GET', '/portfolio/fills', params=params)

    def get_balance(self) -> Dict[str, Any]:
        """
        Get portfolio balance.

        Returns:
            {'balance': int} in cents
        """
        return self._request('GET', '/portfolio/balance')

    # ---- Convenience methods ----

    def get_balance_usd(self) -> float:
        """Get balance as a float in USD."""
        result = self.get_balance()
        balance_cents = result.get('balance', 0)
        return balance_cents / 100.0

    def get_book_metrics(self, ticker: str) -> Dict[str, Any]:
        """
        Get spread and depth metrics from the orderbook.

        Returns:
            {
                'best_bid': float (probability),
                'best_ask': float (probability),
                'spread': float,
                'mid': float,
                'bid_depth_cents': int,
                'ask_depth_cents': int,
                'bid_depth_contracts': int,
                'ask_depth_contracts': int,
            }
        """
        book = self.get_orderbook(ticker)

        # Parse YES side orderbook
        yes_book = book.get('orderbook', book).get('yes', [])
        no_book = book.get('orderbook', book).get('no', [])

        # Best bid for YES = highest YES bid price
        # Best ask for YES = lowest YES ask price (or derived from NO bids)
        # In Kalshi, the YES orderbook has bids and asks directly
        best_bid_cents = 0
        best_ask_cents = 100
        bid_depth_contracts = 0
        ask_depth_contracts = 0

        # YES bids are buy orders for YES contracts
        for level in yes_book:
            price = int(level[0]) if isinstance(level, (list, tuple)) else int(level.get('price', 0))
            qty = int(level[1]) if isinstance(level, (list, tuple)) else int(level.get('quantity', 0))
            if price > best_bid_cents:
                best_bid_cents = price
            bid_depth_contracts += qty

        # NO bids imply YES asks: if someone bids X cents for NO, that means
        # YES can be sold at (100 - X) cents
        for level in no_book:
            price = int(level[0]) if isinstance(level, (list, tuple)) else int(level.get('price', 0))
            qty = int(level[1]) if isinstance(level, (list, tuple)) else int(level.get('quantity', 0))
            implied_ask = 100 - price
            if implied_ask < best_ask_cents:
                best_ask_cents = implied_ask
            ask_depth_contracts += qty

        best_bid = best_bid_cents / 100.0
        best_ask = best_ask_cents / 100.0
        mid = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask < 1.0 else 0.0
        spread = best_ask - best_bid if best_bid > 0 and best_ask < 1.0 else 1.0

        return {
            'best_bid': best_bid,
            'best_ask': best_ask,
            'spread': spread,
            'mid': mid,
            'best_bid_cents': best_bid_cents,
            'best_ask_cents': best_ask_cents,
            'bid_depth_contracts': bid_depth_contracts,
            'ask_depth_contracts': ask_depth_contracts,
        }


if __name__ == '__main__':
    # Quick connectivity test (public endpoints only)
    client = KalshiClient()
    print("Fetching open markets...")
    result = client.get_markets(limit=5, status='open')
    markets = result.get('markets', [])
    print(f"Found {len(markets)} markets:")
    for m in markets:
        ticker = m.get('ticker', '?')
        title = m.get('title', m.get('subtitle', '?'))
        yes_price = m.get('yes_price', m.get('last_price', '?'))
        print(f"  {ticker}: {title} (yes={yes_price}c)")
