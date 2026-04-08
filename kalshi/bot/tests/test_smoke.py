"""
Smoke tests for kalshi-bot - CRITICAL TESTS ONLY

Tests:
1. Config loads correctly
2. Kelly math is correct
3. Dry-run scan runs without error (mocked API)
4. RSA signing produces valid auth headers
5. Risk guard drawdown detection works
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure scripts directory is importable
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---- Test 1: Config loads ----

def test_config_loads():
    """config.example.json parses correctly and has required keys."""
    config_path = REPO_ROOT / "config.example.json"
    assert config_path.exists(), f"Missing {config_path}"

    with open(config_path, 'r') as f:
        config = json.load(f)

    # Required top-level keys
    assert config['bankroll'] == 100.0
    assert config['mispricing_threshold'] == 0.08
    assert config['max_kelly_fraction'] == 0.06
    assert config['max_positions'] == 10
    assert config['scan_limit'] == 200
    assert config['analyze_limit'] == 30
    assert config['min_volume'] == 5000.0
    assert config['min_open_interest'] == 100

    # Required execution sub-keys
    exec_cfg = config['execution']
    assert exec_cfg['max_drawdown_pct'] == 15.0
    assert exec_cfg['max_position_age_hours'] == 72
    assert exec_cfg['min_serenbucks_balance'] == 1.0
    assert exec_cfg['auto_pause_on_exhaustion'] is True

    # Required cron sub-keys
    cron_cfg = config['cron']
    assert cron_cfg['dry_run'] is True
    assert cron_cfg['cron_expression'] == "0 */2 * * *"


# ---- Test 2: Kelly sizing ----

def test_kelly_sizing():
    """Kelly Criterion math produces correct values."""
    import kelly

    # BUY YES: fair_value > market_price
    k = kelly.calculate_kelly_fraction(0.70, 0.50)
    # kelly = (0.70 - 0.50) / (1 - 0.50) = 0.40
    assert abs(k - 0.40) < 0.001

    # BUY NO: fair_value < market_price
    k = kelly.calculate_kelly_fraction(0.30, 0.50)
    # kelly = (0.50 - 0.30) / 0.50 = 0.40
    assert abs(k - 0.40) < 0.001

    # No edge
    k = kelly.calculate_kelly_fraction(0.50, 0.50)
    assert k == 0.0

    # Position size: quarter-Kelly capped at 6%
    size, side = kelly.calculate_position_size(0.70, 0.50, 100.0, 0.06)
    assert side == 'yes'
    # raw kelly = 0.40, quarter = 0.10, capped at 0.06 -> $6.00
    assert size == 6.0

    # Edge calculation
    edge = kelly.calculate_edge(0.70, 0.50)
    assert abs(edge - 0.20) < 0.001

    # Annualized return
    ann = kelly.calculate_annualized_return(0.20, 30)
    # 0.20 / (30/365) = 2.433...
    assert ann > 2.0

    # Cents conversion
    assert kelly.cents_to_probability(50) == 0.50
    assert kelly.probability_to_cents(0.50) == 50
    assert kelly.probability_to_cents(0.01) == 1
    assert kelly.probability_to_cents(0.99) == 99


# ---- Test 3: Dry-run scan ----

def test_dry_run_scan():
    """agent.py runs in dry-run mode without error (mocked APIs)."""
    # Create a temporary config
    config = {
        "bankroll": 100.0,
        "mispricing_threshold": 0.08,
        "max_kelly_fraction": 0.06,
        "max_positions": 10,
        "scan_limit": 5,
        "candidate_limit": 3,
        "analyze_limit": 2,
        "min_volume": 0,
        "min_open_interest": 0,
        "max_divergence": 0.50,
        "min_buy_price": 0.02,
        "min_edge_to_spread_ratio": 1.0,
        "execution": {
            "max_drawdown_pct": 15.0,
            "max_position_age_hours": 72,
            "near_resolution_hours": 24,
            "min_serenbucks_balance": 0.0,
            "auto_pause_on_exhaustion": False,
        },
        "cron": {"dry_run": True},
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(config, f)
        config_path = f.name

    try:
        # Mock environment
        mock_env = {
            'SEREN_API_KEY': 'test-key',
            'KALSHI_API_KEY': 'test-kalshi-key',
        }

        with patch.dict(os.environ, mock_env):
            # Mock the Seren and Kalshi clients
            with patch('agent.SerenClient') as MockSeren, \
                 patch('agent.KalshiClient') as MockKalshi:

                mock_seren = MockSeren.return_value
                mock_seren.get_wallet_balance.return_value = {'balance_usd': 10.0}
                mock_seren.research_market.return_value = "Test research summary"
                mock_seren.estimate_fair_value.return_value = (0.65, 'medium')

                mock_kalshi = MockKalshi.return_value
                mock_kalshi.is_authenticated.return_value = False
                mock_kalshi.get_markets.return_value = {
                    'markets': [
                        {
                            'ticker': 'TEST-MARKET-1',
                            'event_ticker': 'TEST-EVENT',
                            'title': 'Will test event happen?',
                            'yes_price': 50,
                            'volume': 10000,
                            'open_interest': 500,
                            'close_time': '2026-05-01T00:00:00Z',
                        },
                    ],
                    'cursor': None,
                }
                mock_kalshi.get_book_metrics.return_value = {
                    'best_bid': 0.48,
                    'best_ask': 0.52,
                    'spread': 0.04,
                    'mid': 0.50,
                }

                from agent import TradingAgent
                agent = TradingAgent(config_path=config_path, dry_run=True)
                result = agent.run_scan()

                assert result['mode'] == 'dry-run'
                assert result['markets_scanned'] >= 0
                assert isinstance(result['opportunities'], list)
    finally:
        os.unlink(config_path)


# ---- Test 4: Kalshi auth headers ----

def test_kalshi_auth_headers():
    """RSA signing produces valid auth headers with all required fields."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    # Generate a test RSA key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode('utf-8')

    from kalshi_client import KalshiClient
    client = KalshiClient(
        api_key='test-api-key',
        private_key_pem=pem,
    )

    headers = client._sign_request('GET', '/trade-api/v2/markets', '')

    assert 'KALSHI-ACCESS-KEY' in headers
    assert headers['KALSHI-ACCESS-KEY'] == 'test-api-key'
    assert 'KALSHI-ACCESS-SIGNATURE' in headers
    assert 'KALSHI-ACCESS-TIMESTAMP' in headers

    # Signature should be base64-encoded
    import base64
    sig_bytes = base64.b64decode(headers['KALSHI-ACCESS-SIGNATURE'])
    assert len(sig_bytes) > 0

    # Timestamp should be a valid millisecond epoch
    ts = int(headers['KALSHI-ACCESS-TIMESTAMP'])
    assert ts > 1000000000000  # After 2001 in milliseconds


# ---- Test 5: Risk guard drawdown ----

def test_risk_guard_drawdown():
    """Drawdown detection triggers correctly at threshold."""
    from risk_guards import check_drawdown

    # No drawdown
    positions = [
        {'unrealized_pnl': 5.0, 'cost_basis': 50.0},
        {'unrealized_pnl': 2.0, 'cost_basis': 30.0},
    ]
    result = check_drawdown(positions, bankroll=100.0, max_drawdown_pct=15.0)
    assert result['triggered'] is False
    assert result['current_drawdown_pct'] < 0  # positive PnL = negative drawdown

    # Drawdown triggered
    positions = [
        {'unrealized_pnl': -10.0, 'cost_basis': 50.0},
        {'unrealized_pnl': -8.0, 'cost_basis': 30.0},
    ]
    result = check_drawdown(positions, bankroll=100.0, max_drawdown_pct=15.0)
    assert result['triggered'] is True
    assert result['current_drawdown_pct'] >= 15.0

    # Edge case: exactly at threshold
    positions = [
        {'unrealized_pnl': -15.0, 'cost_basis': 50.0},
    ]
    result = check_drawdown(positions, bankroll=100.0, max_drawdown_pct=15.0)
    assert result['triggered'] is True
    assert abs(result['current_drawdown_pct'] - 15.0) < 0.01

    # Zero bankroll should not divide by zero
    result = check_drawdown(positions, bankroll=0.0, max_drawdown_pct=15.0)
    assert result['triggered'] is False
