"""
Unit tests for PolymarketClient initialization and trading guard behavior.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from polymarket_client import PolymarketClient


def _mock_seren():
    client = MagicMock()
    client.call_publisher = MagicMock()
    return client


class TestPolymarketClientInit:
    def test_dry_run_skips_trader_init(self):
        seren = _mock_seren()
        client = PolymarketClient(seren_client=seren, dry_run=True)
        assert client._trader is None

    def test_trading_without_credentials_raises(self):
        seren = _mock_seren()
        client = PolymarketClient(seren_client=seren, dry_run=True)
        with pytest.raises(RuntimeError, match=r"py-clob-client credentials"):
            client.place_order(token_id="t", side="BUY", size=1.0, price=0.5)

    def test_get_markets_uses_polymarket_data_publisher(self):
        seren = _mock_seren()
        seren.call_publisher.return_value = {"body": []}
        client = PolymarketClient(seren_client=seren, dry_run=True)
        client.get_markets(limit=5)
        call_kwargs = seren.call_publisher.call_args.kwargs
        assert call_kwargs["publisher"] == "polymarket-data"

    def test_get_balance_returns_zero_without_trader(self):
        seren = _mock_seren()
        client = PolymarketClient(seren_client=seren, dry_run=True)
        assert client.get_balance() == 0.0
