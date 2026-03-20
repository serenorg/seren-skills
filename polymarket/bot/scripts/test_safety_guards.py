"""
Tests for #220 — annualized return gate, resolution date filter, exit liquidity guard.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import kelly


class TestAnnualizedReturn:
    def test_short_horizon_high_return(self):
        # 10% edge over 30 days = ~122% annualized
        result = kelly.calculate_annualized_return(0.10, 30 / 365)
        assert result > 1.0

    def test_long_horizon_low_return(self):
        # 0.1% edge over 2 years = 0.05% annualized — should fail any hurdle
        result = kelly.calculate_annualized_return(0.001, 2.0)
        assert result < 0.01

    def test_zero_years_returns_inf(self):
        result = kelly.calculate_annualized_return(0.10, 0.0)
        assert result == float('inf')

    def test_hurdle_boundary(self):
        # Exactly 25% annualized: 0.25 edge over 1 year
        result = kelly.calculate_annualized_return(0.25, 1.0)
        assert abs(result - 0.25) < 1e-9


class TestResolutionDateFilter:
    """Test that rank_candidates filters markets resolving too far out."""

    def _make_agent_with_config(self, max_resolution_days=180):
        """Create a minimal agent-like object for testing rank_candidates."""
        from unittest.mock import MagicMock
        from datetime import datetime, timezone, timedelta

        agent = MagicMock()
        agent.max_resolution_days = max_resolution_days
        agent.polymarket = MagicMock()
        agent.polymarket.get_midpoint = MagicMock(return_value=0.4)

        # Import the actual rank_candidates and bind it
        from agent import TradingAgent
        import types
        agent.rank_candidates = types.MethodType(TradingAgent.rank_candidates, agent)
        return agent

    @staticmethod
    def _iso(days_from_now: int) -> str:
        return (datetime.now(timezone.utc) + timedelta(days=days_from_now)).isoformat().replace('+00:00', 'Z')

    @staticmethod
    def _format_end_date(days_from_now: int, variant: str) -> str:
        dt = datetime.now(timezone.utc) + timedelta(days=days_from_now)
        if variant == 'z':
            return dt.isoformat().replace('+00:00', 'Z')
        if variant == 'naive':
            return dt.replace(tzinfo=None).isoformat()
        if variant == 'offset':
            return dt.isoformat()
        raise ValueError(f"Unknown variant: {variant}")

    def test_filters_2028_markets(self):
        agent = self._make_agent_with_config(max_resolution_days=180)
        markets = [
            {'question': 'Near market', 'end_date': self._iso(30),
             'liquidity': 1000, 'volume': 5000, 'token_id': 'tok1'},
            {'question': '2028 market', 'end_date': self._iso(960),
             'liquidity': 1000, 'volume': 5000, 'token_id': 'tok2'},
        ]
        result = agent.rank_candidates(markets, limit=10)
        questions = [m['question'] for m in result]
        assert 'Near market' in questions
        assert '2028 market' not in questions

    def test_keeps_near_term_markets(self):
        agent = self._make_agent_with_config(max_resolution_days=365)
        markets = [
            {'question': 'Soon', 'end_date': self._iso(30),
             'liquidity': 500, 'volume': 1000, 'token_id': 'tok1'},
        ]
        result = agent.rank_candidates(markets, limit=10)
        assert len(result) == 1

    def test_rejects_missing_end_date(self):
        agent = self._make_agent_with_config(max_resolution_days=180)
        markets = [
            {'question': 'Missing date', 'liquidity': 1000, 'volume': 5000, 'token_id': 'tok1'},
            {'question': 'Near market', 'end_date': self._iso(30),
             'liquidity': 1000, 'volume': 5000, 'token_id': 'tok2'},
        ]
        result = agent.rank_candidates(markets, limit=10)
        questions = [m['question'] for m in result]
        assert 'Missing date' not in questions
        assert questions == ['Near market']

    def test_rejects_unparseable_end_date(self):
        agent = self._make_agent_with_config(max_resolution_days=180)
        markets = [
            {'question': 'Bad date', 'end_date': 'not-a-date',
             'liquidity': 1000, 'volume': 5000, 'token_id': 'tok1'},
            {'question': 'Near market', 'end_date': self._iso(30),
             'liquidity': 1000, 'volume': 5000, 'token_id': 'tok2'},
        ]
        result = agent.rank_candidates(markets, limit=10)
        questions = [m['question'] for m in result]
        assert 'Bad date' not in questions
        assert questions == ['Near market']

    @pytest.mark.parametrize('variant', ['z', 'naive', 'offset'])
    def test_accepts_supported_end_date_shapes(self, variant):
        agent = self._make_agent_with_config(max_resolution_days=180)
        markets = [
            {'question': f'Market {variant}',
             'end_date': self._format_end_date(30, variant),
             'liquidity': 1000, 'volume': 5000, 'token_id': f'tok-{variant}',
             'price': 0.18, 'price_source': 'gamma'},
        ]

        result = agent.rank_candidates(markets, limit=10)

        assert len(result) == 1
        assert result[0]['question'] == f'Market {variant}'
        assert result[0]['days_to_resolution'] > 0

    def test_skips_fallback_50_price_when_clob_midpoint_unavailable(self):
        agent = self._make_agent_with_config(max_resolution_days=180)
        agent.polymarket.get_midpoint.side_effect = RuntimeError("missing CLOB creds")
        markets = [
            {'question': 'Fallback price', 'end_date': self._iso(30),
             'liquidity': 1000, 'volume': 5000, 'token_id': 'tok1',
             'price': 0.5, 'price_source': 'gamma_fallback'},
            {'question': 'Valid gamma price', 'end_date': self._iso(30),
             'liquidity': 1000, 'volume': 4000, 'token_id': 'tok2',
             'price': 0.18, 'price_source': 'gamma'},
        ]
        result = agent.rank_candidates(markets, limit=10)
        questions = [m['question'] for m in result]
        assert 'Fallback price' not in questions
        assert questions == ['Valid gamma price']

    def test_prefers_valid_clob_midpoint_over_gamma_price(self):
        agent = self._make_agent_with_config(max_resolution_days=180)
        agent.polymarket.get_midpoint.return_value = 0.22
        markets = [
            {'question': 'Near market', 'end_date': self._iso(30),
             'liquidity': 1000, 'volume': 5000, 'token_id': 'tok1',
             'price': 0.5, 'price_source': 'gamma_fallback'},
        ]
        result = agent.rank_candidates(markets, limit=10)
        assert len(result) == 1
        assert result[0]['price'] == pytest.approx(0.22, abs=0.001)
        assert result[0]['price_source'] == 'clob_midpoint'


class TestEvaluateOpportunityGuards:
    """Test that evaluate_opportunity rejects bad trades."""

    def _make_agent(self):
        from unittest.mock import MagicMock
        agent = MagicMock()
        agent.mispricing_threshold = 0.08
        agent.min_annualized_return = 0.25
        agent.max_positions = 10
        agent.stop_loss_bankroll = 0.0
        agent.bankroll = 100.0
        agent.max_kelly_fraction = 0.06
        agent.positions = MagicMock()
        agent.positions.has_position.return_value = False
        agent.positions.get_all_positions.return_value = []
        agent.positions.get_current_bankroll.return_value = 100.0
        agent.positions.get_available_capital.return_value = 100.0
        agent.polymarket = MagicMock()
        agent.polymarket.get_price.return_value = 0.95  # has exit liquidity

        from agent import TradingAgent
        import types
        agent.evaluate_opportunity = types.MethodType(
            TradingAgent.evaluate_opportunity, agent
        )
        return agent

    def test_rejects_low_annualized_return(self):
        agent = self._make_agent()
        # 99% edge but 3 years out = 33% annualized, passes
        # 0.1% edge but 2 years out = 0.05% annualized, fails
        market = {
            'market_id': 'mkt1', 'price': 0.001, 'question': 'Long shot',
            'token_id': 'tok1', 'no_token_id': 'tok2',
            'days_to_resolution': 730,  # 2 years
        }
        result = agent.evaluate_opportunity(
            market, 'research', fair_value=0.002, confidence='high'
        )
        # Edge is 0.001, annualized = 0.001/2 = 0.0005 = 0.05% — below 25%
        assert result is None

    def test_accepts_good_annualized_return(self):
        agent = self._make_agent()
        market = {
            'market_id': 'mkt2', 'price': 0.50, 'question': 'Good trade',
            'token_id': 'tok1', 'no_token_id': 'tok2',
            'days_to_resolution': 30,  # 1 month out
        }
        result = agent.evaluate_opportunity(
            market, 'research', fair_value=0.65, confidence='high'
        )
        # Edge=0.15, years=30/365=0.082, annualized=1.83 = 183% — well above hurdle
        assert result is not None
        assert result['edge'] == pytest.approx(0.15, abs=0.01)

    def test_rejects_zero_exit_liquidity(self):
        agent = self._make_agent()
        agent.polymarket.get_price.return_value = 0.0  # no bids
        market = {
            'market_id': 'mkt3', 'price': 0.40, 'question': 'No liquidity',
            'token_id': 'tok1', 'no_token_id': 'tok2',
            'days_to_resolution': 30,
        }
        result = agent.evaluate_opportunity(
            market, 'research', fair_value=0.60, confidence='high'
        )
        assert result is None

    def test_rejects_missing_resolution_horizon(self):
        agent = self._make_agent()
        market = {
            'market_id': 'mkt4', 'price': 0.40, 'question': 'Missing horizon',
            'token_id': 'tok1', 'no_token_id': 'tok2',
            'days_to_resolution': 0,
        }
        result = agent.evaluate_opportunity(
            market, 'research', fair_value=0.60, confidence='high'
        )
        assert result is None
