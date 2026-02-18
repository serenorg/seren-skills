"""
Unit tests for pair_selector: base symbol extraction, balance key lookup,
pair scoring, and best-pair selection.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from pair_selector import (
    get_base_symbol,
    get_balance_key,
    score_pair,
    select_best_pair,
)


# ---------------------------------------------------------------------------
# get_base_symbol
# ---------------------------------------------------------------------------

class TestGetBaseSymbol:
    def test_btc_usd(self):
        assert get_base_symbol('XBTUSD') == 'XBT'

    def test_eth_usd(self):
        assert get_base_symbol('ETHUSD') == 'ETH'

    def test_sol_usd(self):
        assert get_base_symbol('SOLUSD') == 'SOL'

    def test_ada_eur(self):
        assert get_base_symbol('ADAEUR') == 'ADA'

    def test_case_insensitive(self):
        assert get_base_symbol('xbtusd') == 'XBT'

    def test_unknown_pair_returns_pair(self):
        # If no known suffix matches, return as-is (uppercased)
        assert get_base_symbol('NOVELTOKEN') == 'NOVELTOKEN'


# ---------------------------------------------------------------------------
# get_balance_key
# ---------------------------------------------------------------------------

class TestGetBalanceKey:
    def test_btc_returns_xxbt(self):
        assert get_balance_key('XBTUSD') == 'XXBT'

    def test_eth_returns_xeth(self):
        assert get_balance_key('ETHUSD') == 'XETH'

    def test_sol_returns_sol(self):
        assert get_balance_key('SOLUSD') == 'SOL'

    def test_ada_returns_ada(self):
        assert get_balance_key('ADAUSD') == 'ADA'

    def test_override_takes_precedence(self):
        # Even for a known pair, explicit override wins
        assert get_balance_key('XBTUSD', override='MYKEY') == 'MYKEY'

    def test_unknown_pair_falls_back_to_base_symbol(self):
        # Unknown asset: base symbol used as key directly
        assert get_balance_key('NOVELUSD') == 'NOVEL'


# ---------------------------------------------------------------------------
# score_pair
# ---------------------------------------------------------------------------

def _make_ticker(pair: str, price: float, bid: float, ask: float,
                 high_24h: float, low_24h: float, volume_24h: float) -> dict:
    return {
        'result': {
            pair: {
                'c': [str(price), '1'],
                'b': [str(bid), '1'],
                'a': [str(ask), '1'],
                'h': ['0', str(high_24h)],
                'l': ['0', str(low_24h)],
                'v': ['0', str(volume_24h)],
            }
        }
    }


class TestScorePair:
    def _seren(self, ticker_response: dict) -> MagicMock:
        seren = MagicMock()
        seren.get_ticker.return_value = ticker_response
        return seren

    def test_returns_score_between_0_and_1(self):
        seren = self._seren(_make_ticker('XBTUSD', 50000, 49990, 50010,
                                         52500, 47500, 1000))
        result = score_pair(seren, 'XBTUSD')
        assert result['error'] is None
        assert 0.0 <= result['score'] <= 1.0

    def test_higher_volume_improves_score(self):
        low_vol = self._seren(_make_ticker('P', 100, 99.9, 100.1, 105, 95, 1_000))
        high_vol = self._seren(_make_ticker('P', 100, 99.9, 100.1, 105, 95, 100_000_000))
        r_low = score_pair(low_vol, 'P')
        r_high = score_pair(high_vol, 'P')
        assert r_high['score'] > r_low['score']

    def test_tight_spread_improves_score(self):
        wide = self._seren(_make_ticker('P', 100, 99, 101, 105, 95, 1_000_000))
        tight = self._seren(_make_ticker('P', 100, 99.95, 100.05, 105, 95, 1_000_000))
        r_wide = score_pair(wide, 'P')
        r_tight = score_pair(tight, 'P')
        assert r_tight['score'] > r_wide['score']

    def test_error_on_empty_ticker(self):
        seren = MagicMock()
        seren.get_ticker.return_value = {'result': {}}
        result = score_pair(seren, 'FAKEUSD')
        assert result['score'] == 0.0
        assert result['error'] is not None

    def test_error_on_exception(self):
        seren = MagicMock()
        seren.get_ticker.side_effect = Exception("network failure")
        result = score_pair(seren, 'XBTUSD')
        assert result['score'] == 0.0
        assert 'network failure' in result['error']

    def test_atr_pct_calculated_correctly(self):
        # price=100, high=105, low=95 → ATR = 10% of 100 = 10%
        seren = self._seren(_make_ticker('P', 100, 99.9, 100.1, 105, 95, 1_000_000))
        result = score_pair(seren, 'P')
        assert result['atr_pct'] == 10.0


# ---------------------------------------------------------------------------
# select_best_pair
# ---------------------------------------------------------------------------

class TestSelectBestPair:
    def _seren_multi(self, scores_by_pair: dict) -> MagicMock:
        """Seren mock that returns different tickers per pair."""
        seren = MagicMock()
        def get_ticker(pair):
            s = scores_by_pair[pair]
            price = s['price']
            spread = price * s['spread_pct'] / 100
            return _make_ticker(pair, price, price - spread/2, price + spread/2,
                                 price * (1 + s['atr_pct']/200),
                                 price * (1 - s['atr_pct']/200),
                                 s['volume_usd'] / price)
        seren.get_ticker.side_effect = get_ticker
        return seren

    def test_selects_highest_scoring_pair(self):
        seren = self._seren_multi({
            'XBTUSD': {'price': 50000, 'atr_pct': 5.0, 'volume_usd': 50_000_000, 'spread_pct': 0.02},
            'SOLUSD': {'price': 100, 'atr_pct': 20.0, 'volume_usd': 100_000, 'spread_pct': 1.0},
        })
        best, best_score, all_scores = select_best_pair(seren, ['XBTUSD', 'SOLUSD'])
        assert best == 'XBTUSD'

    def test_returns_all_scores_sorted_descending(self):
        seren = self._seren_multi({
            'A': {'price': 100, 'atr_pct': 5.0, 'volume_usd': 10_000_000, 'spread_pct': 0.1},
            'B': {'price': 100, 'atr_pct': 20.0, 'volume_usd': 1_000, 'spread_pct': 2.0},
            'C': {'price': 100, 'atr_pct': 5.0, 'volume_usd': 5_000_000, 'spread_pct': 0.1},
        })
        best, _, all_scores = select_best_pair(seren, ['A', 'B', 'C'])
        scores = [s['score'] for s in all_scores]
        assert scores == sorted(scores, reverse=True)

    def test_single_pair_returns_it(self):
        seren = self._seren_multi({
            'XBTUSD': {'price': 50000, 'atr_pct': 5.0, 'volume_usd': 50_000_000, 'spread_pct': 0.02},
        })
        best, _, _ = select_best_pair(seren, ['XBTUSD'])
        assert best == 'XBTUSD'
