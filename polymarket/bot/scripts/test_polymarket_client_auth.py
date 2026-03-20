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

    def test_place_order_uses_live_book_tick_size_and_fee_metadata(self, monkeypatch):
        seren = _mock_seren()
        client = PolymarketClient(seren_client=seren, dry_run=True)

        captured: dict[str, object] = {}

        class FakeTrader:
            def create_order(self, **kwargs):
                captured.update(kwargs)
                return {"orderID": "ORDER-123"}

        client._trader = FakeTrader()

        monkeypatch.setattr(
            "polymarket_live.fetch_book",
            lambda token_id: {"tick_size": "0.001", "neg_risk": True},
        )
        monkeypatch.setattr("polymarket_live.fetch_fee_rate_bps", lambda token_id: 13)
        monkeypatch.setattr("polymarket_live.snap_price", lambda price, tick_size, side: 0.457)

        result = client.place_order(token_id="TOKEN-1", side="SELL", size=5.0, price=0.4567)

        assert result["orderID"] == "ORDER-123"
        assert captured["price"] == 0.457
        assert captured["tick_size"] == "0.001"
        assert captured["neg_risk"] is True
        assert captured["fee_rate_bps"] == 13
