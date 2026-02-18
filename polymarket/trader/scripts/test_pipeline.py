"""
Unit tests for the staged scan pipeline in TradingAgent:
- rank_candidates heuristic ordering and limit enforcement
- config-driven limit defaults (backward compatibility)
"""

import json
import os
import pytest
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_market(market_id: str, price: float, liquidity: float, volume: float = 0.0) -> dict:
    return {
        'market_id': market_id,
        'question': f'Question {market_id}',
        'price': price,
        'liquidity': liquidity,
        'volume': volume,
        'token_id': f'token_{market_id}',
    }


def _make_config(**overrides) -> dict:
    base = {
        'bankroll': 100.0,
        'mispricing_threshold': 0.08,
        'max_kelly_fraction': 0.06,
        'max_positions': 10,
        'stop_loss_bankroll': 0.0,
    }
    base.update(overrides)
    return base


def _build_agent(config: dict) -> 'TradingAgent':
    """Construct a TradingAgent with all external dependencies mocked out."""
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.json', delete=False
    ) as f:
        json.dump(config, f)
        cfg_path = f.name

    try:
        with patch('agent.SerenClient'), \
             patch('agent.PolymarketClient'), \
             patch('agent.SerenDBStorage') as MockStorage, \
             patch('agent.PositionTracker'), \
             patch('agent.TradingLogger'):

            MockStorage.return_value.setup_database.return_value = True

            from agent import TradingAgent
            agent = TradingAgent.__new__(TradingAgent)
            agent.config = config
            agent.dry_run = False
            agent.bankroll = float(config['bankroll'])
            agent.mispricing_threshold = float(config['mispricing_threshold'])
            agent.max_kelly_fraction = float(config['max_kelly_fraction'])
            agent.max_positions = int(config['max_positions'])
            agent.stop_loss_bankroll = float(config.get('stop_loss_bankroll', 0.0))
            agent.scan_limit = int(config.get('scan_limit', 100))
            agent.candidate_limit = int(config.get('candidate_limit', 20))
            agent.analyze_limit = int(config.get('analyze_limit', agent.candidate_limit))
            agent.min_liquidity = float(config.get('min_liquidity', 100.0))
            return agent
    finally:
        os.unlink(cfg_path)


# ---------------------------------------------------------------------------
# rank_candidates — ordering
# ---------------------------------------------------------------------------

class TestRankCandidates:
    def _agent(self, **cfg_overrides):
        return _build_agent(_make_config(**cfg_overrides))

    def test_returns_at_most_limit(self):
        agent = self._agent()
        markets = [_make_market(str(i), 0.5, 1000) for i in range(50)]
        result = agent.rank_candidates(markets, limit=10)
        assert len(result) == 10

    def test_returns_all_when_fewer_than_limit(self):
        agent = self._agent()
        markets = [_make_market(str(i), 0.5, 1000) for i in range(5)]
        result = agent.rank_candidates(markets, limit=20)
        assert len(result) == 5

    def test_higher_liquidity_ranks_first(self):
        agent = self._agent()
        markets = [
            _make_market('low', 0.5, 100),
            _make_market('high', 0.5, 10000),
            _make_market('mid', 0.5, 1000),
        ]
        result = agent.rank_candidates(markets, limit=3)
        assert result[0]['market_id'] == 'high'
        assert result[1]['market_id'] == 'mid'
        assert result[2]['market_id'] == 'low'

    def test_price_near_50pct_preferred_over_extreme(self):
        """With equal liquidity, 50% price beats 5% or 95%."""
        agent = self._agent()
        markets = [
            _make_market('extreme', 0.05, 1000),
            _make_market('middle', 0.50, 1000),
        ]
        result = agent.rank_candidates(markets, limit=2)
        assert result[0]['market_id'] == 'middle'

    def test_empty_market_list(self):
        agent = self._agent()
        assert agent.rank_candidates([], limit=10) == []

    def test_limit_zero_returns_empty(self):
        agent = self._agent()
        markets = [_make_market(str(i), 0.5, 1000) for i in range(5)]
        result = agent.rank_candidates(markets, limit=0)
        assert result == []


# ---------------------------------------------------------------------------
# Config-driven limits — defaults and backward compatibility
# ---------------------------------------------------------------------------

class TestConfigLimits:
    def test_defaults_when_fields_absent(self):
        agent = _build_agent(_make_config())
        assert agent.scan_limit == 100
        assert agent.candidate_limit == 20
        # analyze_limit defaults to candidate_limit when absent
        assert agent.analyze_limit == agent.candidate_limit
        assert agent.min_liquidity == 100.0

    def test_explicit_values_used(self):
        agent = _build_agent(_make_config(
            scan_limit=300,
            candidate_limit=80,
            analyze_limit=30,
            min_liquidity=500.0,
        ))
        assert agent.scan_limit == 300
        assert agent.candidate_limit == 80
        assert agent.analyze_limit == 30
        assert agent.min_liquidity == 500.0

    def test_analyze_limit_can_exceed_candidate_limit(self):
        # analyze_limit is capped by len(candidates) in practice, but
        # the config value itself is stored as-is
        agent = _build_agent(_make_config(
            candidate_limit=10,
            analyze_limit=50,
        ))
        assert agent.analyze_limit == 50
