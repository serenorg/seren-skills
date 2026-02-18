"""
Pair Selector - Scores Kraken pairs for grid-trading suitability.

Grid trading profits from range-bound, liquid markets with tight spreads.
This module scores candidate pairs using only live ticker data (no extra API calls)
and selects the best pair to trade at the time the bot starts.
"""

import math
from typing import Any, Dict, List, Optional, Tuple


# Kraken account balance key for each base asset.
# Kraken uses X-prefixed keys for legacy "crypto" assets; newer assets are direct.
KRAKEN_BALANCE_KEYS: Dict[str, str] = {
    'XBT': 'XXBT',
    'ETH': 'XETH',
    'LTC': 'XLTC',
    'XRP': 'XXRP',
    'XMR': 'XXMR',
    'ZEC': 'XZEC',
    'SOL': 'SOL',
    'ADA': 'ADA',
    'DOT': 'DOT',
    'LINK': 'LINK',
    'MATIC': 'MATIC',
    'AVAX': 'AVAX',
    'ATOM': 'ATOM',
    'UNI': 'UNI',
    'DOGE': 'DOGE',
    'SHIB': 'SHIB',
    'AAVE': 'AAVE',
    'FIL': 'FIL',
    'ALGO': 'ALGO',
    'NEAR': 'NEAR',
    'APT': 'APT',
    'ARB': 'ARB',
    'OP': 'OP',
    'SUI': 'SUI',
    'TRX': 'TRX',
    'FTM': 'FTM',
}

# Quote currencies stripped when extracting base symbol from a pair name.
_QUOTE_SUFFIXES = ('USDT', 'USDC', 'USD', 'EUR', 'GBP', 'BTC', 'ETH')


def get_base_symbol(pair: str) -> str:
    """
    Extract the base asset symbol from a Kraken pair name.

    Examples:
        'XBTUSD'  -> 'XBT'
        'ETHUSD'  -> 'ETH'
        'SOLUSD'  -> 'SOL'
        'ADAEUR'  -> 'ADA'
    """
    pair = pair.upper()
    for suffix in _QUOTE_SUFFIXES:
        if pair.endswith(suffix) and len(pair) > len(suffix):
            return pair[:-len(suffix)]
    return pair


def get_balance_key(pair: str, override: Optional[str] = None) -> str:
    """
    Return the Kraken account balance dict key for the base asset of a pair.

    Args:
        pair: Trading pair (e.g., 'XBTUSD')
        override: Explicit override from config (e.g., 'XXBT') — takes precedence

    Returns:
        Kraken balance key string (e.g., 'XXBT', 'SOL')
    """
    if override:
        return override
    base = get_base_symbol(pair)
    return KRAKEN_BALANCE_KEYS.get(base, base)


def score_pair(seren: Any, pair: str) -> Dict[str, Any]:
    """
    Score a pair for grid-trading suitability using live ticker data.

    Scoring factors (grid strategy prefers):
    - ATR 2–8% of price: ideal volatility band for grid capture
    - High 24h volume (liquidity)
    - Tight bid-ask spread

    Args:
        seren: SerenClient instance
        pair: Kraken pair (e.g., 'ETHUSD')

    Returns:
        Dict with keys: score, atr_pct, volume_usd_24h, spread_pct,
                        current_price, error
    """
    try:
        ticker = seren.get_ticker(pair)
        result = ticker.get('result', {})
        if not result:
            return {'pair': pair, 'score': 0.0, 'error': f'No ticker data for {pair}'}

        data = list(result.values())[0]
        current_price = float(data['c'][0])
        bid = float(data['b'][0])
        ask = float(data['a'][0])
        volume_base_24h = float(data['v'][1])   # 24h volume in base currency
        high_24h = float(data['h'][1])
        low_24h = float(data['l'][1])

        spread_pct = (ask - bid) / current_price * 100
        atr_pct = (high_24h - low_24h) / current_price * 100
        volume_usd_24h = volume_base_24h * current_price

        # ATR score: peak at 5% (ideal grid range). Penalise too-low (<1%) and too-high (>15%).
        atr_score = max(0.0, 1.0 - abs(atr_pct - 5.0) / 10.0)

        # Volume score: log-normalised, calibrated so $50M ≈ 1.0
        vol_score = min(1.0, math.log1p(volume_usd_24h) / math.log1p(50_000_000))

        # Spread score: penalise spread above 0.5%
        spread_score = max(0.0, 1.0 - spread_pct / 0.5)

        # Composite: ATR matters most for grid profitability
        score = atr_score * 0.50 + vol_score * 0.35 + spread_score * 0.15

        return {
            'pair': pair,
            'score': round(score, 4),
            'atr_pct': round(atr_pct, 2),
            'volume_usd_24h': round(volume_usd_24h, 0),
            'spread_pct': round(spread_pct, 4),
            'current_price': current_price,
            'error': None,
        }

    except Exception as e:
        return {'pair': pair, 'score': 0.0, 'error': str(e), 'current_price': None}


def select_best_pair(
    seren: Any,
    pairs: List[str]
) -> Tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
    """
    Score all candidate pairs and return the best one.

    Args:
        seren: SerenClient instance
        pairs: List of Kraken pair strings to evaluate

    Returns:
        (best_pair, best_score_details, all_scores_sorted)
    """
    scores = [score_pair(seren, pair) for pair in pairs]
    scores.sort(key=lambda s: s['score'], reverse=True)
    best = scores[0]
    return best['pair'], best, scores
