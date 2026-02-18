"""
Seren Gateway API Client - Routes Coinbase Exchange calls through Seren Gateway

All Coinbase Exchange API calls go through api.serendb.com/publishers/coinbase-trading
Auth headers (CB-ACCESS-*) are passed through to Coinbase by the gateway.
"""

import base64
import hashlib
import hmac
import json
import time
import requests
from typing import Any, Dict, List, Optional


class SerenClient:
    """Wrapper for Seren Gateway API (Coinbase Exchange publisher)"""

    PUBLISHER = 'coinbase-trading'

    def __init__(
        self,
        seren_api_key: str,
        cb_access_key: str,
        cb_secret: str,
        cb_passphrase: str,
        base_url: str = 'https://api.serendb.com'
    ):
        """
        Initialize Seren client with Coinbase credentials

        Args:
            seren_api_key: Seren API key (starts with 'sb_')
            cb_access_key: Coinbase API key
            cb_secret: Coinbase API secret (base64-encoded)
            cb_passphrase: Coinbase API passphrase
            base_url: Seren Gateway base URL
        """
        self.seren_api_key = seren_api_key
        self.cb_access_key = cb_access_key
        self.cb_secret = cb_secret
        self.cb_passphrase = cb_passphrase
        self.base_url = base_url

    def _sign(self, method: str, path: str, body_str: str = '') -> tuple:
        """
        Generate Coinbase Exchange HMAC-SHA256 signature

        Args:
            method: HTTP method (GET, POST, DELETE)
            path: Request path including query string (e.g., '/orders?product_id=BTC-USD')
            body_str: Request body as JSON string (empty string for GET/DELETE)

        Returns:
            (signature_b64, timestamp_str) tuple
        """
        timestamp = str(time.time())
        message = timestamp + method.upper() + path + body_str
        secret_bytes = base64.b64decode(self.cb_secret)
        sig = hmac.new(secret_bytes, message.encode('utf-8'), hashlib.sha256)
        return base64.b64encode(sig.digest()).decode('utf-8'), timestamp

    def _call(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None
    ) -> Any:
        """
        Make an authenticated request through the Seren Gateway

        Args:
            method: HTTP method
            path: Coinbase API path (e.g., '/accounts')
            body: Request body dict (for POST)
            params: Query parameters (for GET)

        Returns:
            Parsed response (list or dict)

        Raises:
            requests.HTTPError: On API errors
        """
        # Build the full path including query string for signing
        query_string = ''
        if params:
            query_string = '?' + '&'.join(f'{k}={v}' for k, v in params.items())
        full_path = path + query_string

        body_str = json.dumps(body) if body else ''
        signature, timestamp = self._sign(method, full_path, body_str)

        url = f"{self.base_url}/publishers/{self.PUBLISHER}{path}"
        headers = {
            'Authorization': f'Bearer {self.seren_api_key}',
            'CB-ACCESS-KEY': self.cb_access_key,
            'CB-ACCESS-SIGN': signature,
            'CB-ACCESS-TIMESTAMP': timestamp,
            'CB-ACCESS-PASSPHRASE': self.cb_passphrase,
            'Content-Type': 'application/json',
        }

        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            data=body_str if body_str else None,
            params=params,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()

        # Unwrap Seren Gateway envelope if present
        if isinstance(data, dict) and 'body' in data:
            return data['body']
        return data

    # ========== Account ==========

    def get_accounts(self) -> List[Dict[str, Any]]:
        """
        List all Coinbase Exchange accounts

        Returns:
            List of account objects with id, currency, balance, available
        """
        return self._call('GET', '/accounts')

    def get_account_balance(self, currency: str) -> float:
        """
        Get available balance for a currency

        Args:
            currency: Currency symbol (e.g., 'BTC', 'USD')

        Returns:
            Available balance as float (0.0 if not found)
        """
        accounts = self.get_accounts()
        for account in accounts:
            if account.get('currency') == currency:
                return float(account.get('available', 0))
        return 0.0

    # ========== Products ==========

    def get_products(self) -> List[Dict[str, Any]]:
        """
        List all tradable products on Coinbase Exchange

        Returns:
            List of product objects (id, base_currency, quote_currency, status, etc.)
        """
        return self._call('GET', '/products')

    def get_usd_products(self) -> List[Dict[str, Any]]:
        """
        List all online USD-quoted products

        Returns:
            Filtered list of active USD trading pairs
        """
        products = self.get_products()
        return [
            p for p in products
            if p.get('quote_currency') == 'USD' and p.get('status') == 'online'
        ]

    def validate_product(self, product_id: str) -> bool:
        """
        Check that a product exists and is online

        Args:
            product_id: Product ID (e.g., 'BTC-USD')

        Returns:
            True if product is valid and online
        """
        products = self.get_products()
        for p in products:
            if p.get('id') == product_id and p.get('status') == 'online':
                return True
        return False

    # ========== Orders ==========

    def get_open_orders(self, product_id: str) -> List[Dict[str, Any]]:
        """
        List open orders for a product

        Args:
            product_id: Product ID (e.g., 'BTC-USD')

        Returns:
            List of open order objects
        """
        return self._call(
            'GET',
            '/orders',
            params={'product_id': product_id, 'status': 'open'}
        )

    def place_limit_order(
        self,
        side: str,
        product_id: str,
        price: float,
        size: float,
        post_only: bool = True
    ) -> Dict[str, Any]:
        """
        Place a limit order on Coinbase Exchange

        Args:
            side: 'buy' or 'sell'
            product_id: Product ID (e.g., 'BTC-USD')
            price: Limit price (USD)
            size: Order size in base currency (BTC)
            post_only: If True, reject if would take liquidity (ensures maker fee)

        Returns:
            Order response with id, status, price, size, etc.
        """
        body = {
            'type': 'limit',
            'side': side,
            'product_id': product_id,
            'price': f'{price:.2f}',
            'size': f'{size:.8f}',
            'post_only': post_only,
            'time_in_force': 'GTC',
        }
        return self._call('POST', '/orders', body=body)

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order

        Args:
            order_id: Order ID to cancel

        Returns:
            True on success
        """
        self._call('DELETE', f'/orders/{order_id}')
        return True

    def cancel_all_orders(self, product_id: str) -> int:
        """
        Cancel all open orders for a product by fetching and cancelling each

        Args:
            product_id: Product ID (e.g., 'BTC-USD')

        Returns:
            Number of orders cancelled
        """
        open_orders = self.get_open_orders(product_id)
        cancelled = 0
        for order in open_orders:
            try:
                self.cancel_order(order['id'])
                cancelled += 1
            except Exception:
                pass
        return cancelled
