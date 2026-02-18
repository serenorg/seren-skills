"""
Polymarket Client - Wrapper for Polymarket CLOB API via Seren

Uses the polymarket-trading-serenai publisher to:
- Get market data (prices, order book, positions)
- Place and cancel orders
- Track positions and P&L
"""

import os
import json
from typing import Dict, List, Any, Optional
from seren_client import SerenClient


class PolymarketClient:
    """Client for Polymarket CLOB API via Seren publisher"""

    def __init__(
        self,
        seren_client: SerenClient,
        poly_api_key: Optional[str] = None,
        poly_passphrase: Optional[str] = None,
        poly_secret: Optional[str] = None,
        poly_address: Optional[str] = None
    ):
        """
        Initialize Polymarket client

        Args:
            seren_client: Seren client instance
            poly_api_key: Polymarket API key (from env if not provided)
            poly_passphrase: Polymarket passphrase
            poly_secret: Polymarket secret
            poly_address: Polymarket wallet address
        """
        self.seren = seren_client

        # Get credentials from env if not provided
        self.poly_api_key = poly_api_key or os.getenv('POLY_API_KEY')
        self.poly_passphrase = poly_passphrase or os.getenv('POLY_PASSPHRASE')
        self.poly_secret = poly_secret or os.getenv('POLY_SECRET')
        self.poly_address = poly_address or os.getenv('POLY_ADDRESS')

        if not all([self.poly_api_key, self.poly_passphrase, self.poly_address]):
            raise ValueError(
                "Polymarket credentials required: POLY_API_KEY, POLY_PASSPHRASE, POLY_ADDRESS"
            )

    def _get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for Polymarket API"""
        return {
            'POLY_API_KEY': self.poly_api_key,
            'POLY_PASSPHRASE': self.poly_passphrase,
            'POLY_ADDRESS': self.poly_address
        }

    def get_markets(self, limit: int = 500, active: bool = True) -> List[Dict]:
        """
        Get list of prediction markets

        Args:
            limit: Max markets to return
            active: Only active markets

        Returns:
            List of market dicts with format:
            {
                'market_id': str,
                'question': str,
                'token_id': str,
                'price': float (0.0-1.0),
                'volume': float,
                'liquidity': float,
                'end_date': str
            }
        """
        # Build query parameters for GET request
        # Use query params instead of body for GET requests
        params = f"?limit={limit}&active={'true' if active else 'false'}&closed=false"

        # Call polymarket-data publisher to get markets
        response = self.seren.call_publisher(
            publisher='polymarket-data',
            method='GET',
            path=f'/markets{params}'
        )

        markets = []

        # Parse response - publisher returns data in 'body' field
        market_list = response.get('body', [])
        if not market_list and 'data' in response:
            # Fallback for older API versions
            market_list = response.get('data', [])

        for market_data in market_list:
            # Skip closed markets
            if market_data.get('closed', False):
                continue

            # Extract relevant fields (API uses camelCase)
            market_id = market_data.get('conditionId') or market_data.get('id')
            question = market_data.get('question', '')

            # Get token IDs - they're stored as a JSON string
            clob_token_ids_str = market_data.get('clobTokenIds', '[]')
            try:
                token_ids = json.loads(clob_token_ids_str) if isinstance(clob_token_ids_str, str) else clob_token_ids_str
            except:
                token_ids = []

            # Use first token ID (typically YES outcome for binary markets)
            if not token_ids or len(token_ids) == 0:
                continue  # Skip markets without tokens

            token_id = token_ids[0]

            # Get current price from outcomePrices (first is YES for binary)
            outcome_prices = market_data.get('outcomePrices', ['0.5'])
            try:
                price = float(outcome_prices[0]) if outcome_prices else 0.5
            except:
                price = 0.5

            # Volume and liquidity
            volume = float(market_data.get('volume', 0))
            liquidity = float(market_data.get('liquidity', 0))

            # End date (check both camelCase and snake_case)
            end_date = market_data.get('endDateIso') or market_data.get('end_date_iso', '')

            # Only include markets with sufficient liquidity
            if liquidity < 100:  # Skip markets with < $100 liquidity
                continue

            markets.append({
                'market_id': market_id,
                'question': question,
                'token_id': token_id,
                'price': price,
                'volume': volume,
                'liquidity': liquidity,
                'end_date': end_date
            })

        return markets[:limit]

    def get_price(self, token_id: str, side: str) -> float:
        """
        Get current price for a token

        Args:
            token_id: ERC1155 token ID
            side: 'BUY' or 'SELL'

        Returns:
            Price as float (0.0-1.0)
        """
        response = self.seren.call_publisher(
            publisher='polymarket-trading-serenai',
            method='GET',
            path='/price',
            headers=self._get_auth_headers(),
            body={'token_id': token_id, 'side': side}
        )
        return float(response.get('price', 0))

    def get_midpoint(self, token_id: str) -> float:
        """
        Get midpoint price (average of best bid and ask)

        Args:
            token_id: ERC1155 token ID

        Returns:
            Midpoint price as float (0.0-1.0)
        """
        response = self.seren.call_publisher(
            publisher='polymarket-trading-serenai',
            method='GET',
            path='/midpoint',
            headers=self._get_auth_headers(),
            body={'token_id': token_id}
        )
        return float(response.get('mid', 0))

    def get_positions(self) -> List[Dict]:
        """
        Get current positions

        Returns:
            List of position dicts with market, size, entry_price, etc.
        """
        response = self.seren.call_publisher(
            publisher='polymarket-trading-serenai',
            method='GET',
            path='/positions',
            headers=self._get_auth_headers()
        )
        return response.get('data', [])

    def get_open_orders(self, market: Optional[str] = None) -> List[Dict]:
        """
        Get open orders

        Args:
            market: Filter by market ID (optional)

        Returns:
            List of open orders
        """
        body = {}
        if market:
            body['market'] = market

        response = self.seren.call_publisher(
            publisher='polymarket-trading-serenai',
            method='GET',
            path='/orders',
            headers=self._get_auth_headers(),
            body=body if body else None
        )
        return response.get('data', [])

    def place_order(
        self,
        token_id: str,
        side: str,
        size: float,
        price: float,
        order_type: str = 'GTC'
    ) -> Dict:
        """
        Place an order

        Note: The polymarket-trading-serenai publisher handles EIP-712 signing
        server-side using the credentials provided in headers.

        Args:
            token_id: ERC1155 token ID
            side: 'BUY' or 'SELL'
            size: Order size in USDC
            price: Limit price (0.0-1.0)
            order_type: Order type (GTC, GTD, FOK, FAK)

        Returns:
            Order details
        """
        order_data = {
            'token_id': token_id,
            'side': side,
            'size': str(size),
            'price': str(price),
            'type': order_type
        }

        response = self.seren.call_publisher(
            publisher='polymarket-trading-serenai',
            method='POST',
            path='/order',
            headers=self._get_auth_headers(),
            body=order_data
        )
        return response

    def cancel_order(self, order_id: str) -> Dict:
        """
        Cancel an open order

        Args:
            order_id: Order ID to cancel

        Returns:
            Cancellation confirmation
        """
        response = self.seren.call_publisher(
            publisher='polymarket-trading-serenai',
            method='DELETE',
            path='/order',
            headers=self._get_auth_headers(),
            body={'orderID': order_id}
        )
        return response

    def get_balance(self) -> float:
        """
        Get USDC balance from Polymarket wallet

        Returns:
            Balance in USDC
        """
        try:
            response = self.seren.call_publisher(
                publisher='polymarket-trading-serenai',
                method='GET',
                path='/balance',
                headers=self._get_auth_headers()
            )

            # Parse response - may be wrapped in 'body' field
            balance_data = response.get('body', response)

            # Balance should be in 'balance' field
            if isinstance(balance_data, dict):
                return float(balance_data.get('balance', 0.0))
            elif isinstance(balance_data, (int, float)):
                return float(balance_data)
            else:
                return 0.0

        except Exception as e:
            # If balance endpoint fails, return 0.0
            # The bot will still work, just won't show balance
            return 0.0
