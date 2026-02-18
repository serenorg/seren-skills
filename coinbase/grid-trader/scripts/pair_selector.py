"""
Pair Selector - Validates and lists Coinbase Exchange trading pairs

Note: The coinbase-trading publisher does not expose a ticker endpoint,
so live ATR/volume scoring (as used in the Kraken pair selector) is not
available. This module validates pairs against GET /products and lists
active USD pairs for user reference.
"""

from typing import List, Optional


def get_usd_pairs(seren) -> List[str]:
    """
    List all active USD-quoted trading pairs on Coinbase Exchange

    Args:
        seren: SerenClient instance

    Returns:
        Sorted list of product IDs (e.g., ['BTC-USD', 'ETH-USD', ...])
    """
    products = seren.get_usd_products()
    return sorted(p['id'] for p in products)


def validate_pair(seren, product_id: str) -> bool:
    """
    Confirm a product ID is valid and online on Coinbase Exchange

    Args:
        seren: SerenClient instance
        product_id: Product ID to validate (e.g., 'BTC-USD')

    Returns:
        True if valid and online
    """
    return seren.validate_product(product_id)


def get_base_currency(product_id: str) -> str:
    """
    Extract base currency from a Coinbase product ID

    Args:
        product_id: Product ID (e.g., 'BTC-USD')

    Returns:
        Base currency symbol (e.g., 'BTC')
    """
    return product_id.split('-')[0]


def get_quote_currency(product_id: str) -> str:
    """
    Extract quote currency from a Coinbase product ID

    Args:
        product_id: Product ID (e.g., 'BTC-USD')

    Returns:
        Quote currency symbol (e.g., 'USD')
    """
    return product_id.split('-')[1]
