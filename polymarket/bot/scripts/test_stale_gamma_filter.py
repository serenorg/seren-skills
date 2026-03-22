"""
Tests for #250 — stale 50/50 Gamma price filtering.

Verifies the end-to-end chain: polymarket_client builds the outcomePrices CSV,
agent.py parses it, and stale markets are excluded before LLM evaluation.
"""

import sys
import types
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))


def _make_agent(clob_midpoint=None):
    """Create a mock agent with rank_candidates bound."""
    from agent import TradingAgent

    agent = MagicMock()
    agent.max_resolution_days = 180
    agent.stale_price_demotion = 0.1
    agent.polymarket = MagicMock()
    if clob_midpoint is not None:
        agent.polymarket.get_midpoint = MagicMock(return_value=clob_midpoint)
    else:
        agent.polymarket.get_midpoint = MagicMock(side_effect=Exception("no CLOB"))
    agent.rank_candidates = types.MethodType(TradingAgent.rank_candidates, agent)
    return agent


def _end_date(days=30):
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat().replace('+00:00', 'Z')


def _market(question, outcome_csv, liquidity=1_000_000, volume=500_000, price=None, price_source='gamma'):
    """Build a market dict matching polymarket_client output."""
    if price is None:
        parts = outcome_csv.split(',') if outcome_csv else []
        price = float(parts[0]) if parts else 0.5
    return {
        'market_id': f'mkt-{question[:8]}',
        'question': question,
        'token_id': f'tok-{question[:8]}',
        'no_token_id': f'no-{question[:8]}',
        'outcomePrices': outcome_csv,
        'price': price,
        'price_source': price_source,
        'volume': volume,
        'liquidity': liquidity,
        'end_date': _end_date(30),
    }


class TestStaleGammaPreFilter:
    """Stale 50/50 markets must be removed before they reach LLM evaluation."""

    def test_stale_5050_excluded_from_candidates(self):
        agent = _make_agent(clob_midpoint=None)
        markets = [
            _market('Real market', '0.35,0.65'),
            _market('Stale longshot', '0.5,0.5'),
            _market('Another stale', '0.50,0.50'),
        ]
        result = agent.rank_candidates(markets, limit=10)
        questions = [m['question'] for m in result]
        assert 'Real market' in questions
        assert 'Stale longshot' not in questions
        assert 'Another stale' not in questions

    def test_non_stale_market_passes_through(self):
        agent = _make_agent(clob_midpoint=0.35)
        markets = [
            _market('Legit low', '0.05,0.95'),
            _market('Legit mid', '0.40,0.60'),
        ]
        result = agent.rank_candidates(markets, limit=10)
        assert len(result) == 2

    def test_missing_outcome_prices_uses_fallback_guard(self):
        """Markets with no outcomePrices and gamma_fallback source are rejected."""
        agent = _make_agent(clob_midpoint=None)
        markets = [
            _market('No prices', '', price=0.5, price_source='gamma_fallback'),
            _market('Has prices', '0.30,0.70'),
        ]
        result = agent.rank_candidates(markets, limit=10)
        questions = [m['question'] for m in result]
        assert 'Has prices' in questions
        # 'No prices' should be rejected by the fallback guard (stale_price_skips)
        assert 'No prices' not in questions


class TestOutcomePricesPassthrough:
    """polymarket_client must pass outcomePrices as comma-separated floats."""

    def test_json_array_string_parsed_to_csv(self):
        """Gamma API returns outcomePrices as JSON string '["0.5","0.5"]'."""
        import json
        from polymarket_client import PolymarketClient

        client = PolymarketClient.__new__(PolymarketClient)
        client.seren = MagicMock()
        client._trader = None

        raw_api_response = [
            {
                'conditionId': 'cond-1',
                'question': 'Test stale',
                'clobTokenIds': json.dumps(['yes-tok', 'no-tok']),
                'outcomePrices': json.dumps(['0.5', '0.5']),
                'volume': '1000',
                'liquidity': '5000',
                'endDateIso': _end_date(30),
            },
            {
                'conditionId': 'cond-2',
                'question': 'Test real',
                'clobTokenIds': json.dumps(['yes-tok-2', 'no-tok-2']),
                'outcomePrices': json.dumps(['0.03', '0.97']),
                'volume': '2000',
                'liquidity': '8000',
                'endDateIso': _end_date(30),
            },
        ]

        client.seren = MagicMock()
        client.seren.call_publisher = MagicMock(return_value={'body': raw_api_response})

        markets = client.get_markets(limit=10, active=True)

        stale = next(m for m in markets if m['question'] == 'Test stale')
        assert stale['outcomePrices'] == '0.5,0.5'
        assert stale['price'] == 0.5
        assert stale['price_source'] == 'gamma'

        real = next(m for m in markets if m['question'] == 'Test real')
        assert real['outcomePrices'] == '0.03,0.97'
        assert real['price'] == 0.03
        assert real['price_source'] == 'gamma'

    def test_list_format_parsed_to_csv(self):
        """Some API responses return outcomePrices as a native list."""
        import json
        from polymarket_client import PolymarketClient

        client = PolymarketClient.__new__(PolymarketClient)
        client.seren = MagicMock()
        client._trader = None

        raw_api_response = [
            {
                'conditionId': 'cond-3',
                'question': 'List format',
                'clobTokenIds': json.dumps(['tok-a', 'tok-b']),
                'outcomePrices': [0.5, 0.5],
                'volume': '3000',
                'liquidity': '6000',
                'endDateIso': _end_date(30),
            },
        ]

        client.seren.call_publisher = MagicMock(return_value={'body': raw_api_response})
        markets = client.get_markets(limit=10, active=True)

        m = markets[0]
        assert m['outcomePrices'] == '0.5,0.5'
        assert m['price'] == 0.5


class TestEndToEnd:
    """Full chain: polymarket_client output → agent.rank_candidates → filtered."""

    def test_stale_market_never_reaches_llm(self):
        """A stale 50/50 market with high liquidity must not appear in candidates."""
        agent = _make_agent(clob_midpoint=None)

        # Simulate what polymarket_client returns for a mix of markets
        markets = [
            _market('Will Curacao win FIFA World Cup?', '0.5,0.5', liquidity=2_000_000),
            _market('Will Haiti win FIFA World Cup?', '0.5,0.5', liquidity=1_800_000),
            _market('Russia Ukraine ceasefire by April?', '0.15,0.85', liquidity=500_000),
            _market('BTC above 100k end of Q2?', '0.62,0.38', liquidity=300_000),
        ]

        result = agent.rank_candidates(markets, limit=10)
        questions = [m['question'] for m in result]

        # Stale markets excluded despite massive liquidity
        assert 'Will Curacao win FIFA World Cup?' not in questions
        assert 'Will Haiti win FIFA World Cup?' not in questions

        # Real markets survive
        assert 'Russia Ukraine ceasefire by April?' in questions
        assert 'BTC above 100k end of Q2?' in questions
