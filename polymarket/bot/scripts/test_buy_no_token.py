"""
Tests for BUY NO token execution path and book-level fallback.
Covers: #218 — SELL orders must BUY NO tokens at live ask price.
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


class TestGetMarketsExposesNoTokenId:
    def test_no_token_id_present_when_two_clob_tokens(self):
        seren = _mock_seren()
        seren.call_publisher.return_value = {
            "body": [
                {
                    "conditionId": "0xabc",
                    "question": "Will X happen?",
                    "clobTokenIds": '["YES_TOKEN", "NO_TOKEN"]',
                    "outcomePrices": '["0.6", "0.4"]',
                    "volume": "5000",
                    "liquidity": "10000",
                    "endDateIso": "2026-12-31",
                    "active": True,
                }
            ]
        }
        client = PolymarketClient(seren_client=seren, dry_run=True)
        markets = client.get_markets(limit=10)
        assert len(markets) == 1
        assert markets[0]["token_id"] == "YES_TOKEN"
        assert markets[0]["no_token_id"] == "NO_TOKEN"

    def test_no_token_id_none_when_single_clob_token(self):
        seren = _mock_seren()
        seren.call_publisher.return_value = {
            "body": [
                {
                    "conditionId": "0xabc",
                    "question": "Will X happen?",
                    "clobTokenIds": '["YES_TOKEN"]',
                    "outcomePrices": '["0.5"]',
                    "volume": "5000",
                    "liquidity": "10000",
                    "endDateIso": "2026-12-31",
                    "active": True,
                }
            ]
        }
        client = PolymarketClient(seren_client=seren, dry_run=True)
        markets = client.get_markets(limit=10)
        assert markets[0]["no_token_id"] is None

    def test_marks_missing_outcome_prices_as_gamma_fallback(self):
        seren = _mock_seren()
        seren.call_publisher.return_value = {
            "body": [
                {
                    "conditionId": "0xabc",
                    "question": "Will X happen?",
                    "clobTokenIds": '["YES_TOKEN", "NO_TOKEN"]',
                    "volume": "5000",
                    "liquidity": "10000",
                    "endDateIso": "2026-12-31",
                    "active": True,
                }
            ]
        }
        client = PolymarketClient(seren_client=seren, dry_run=True)
        markets = client.get_markets(limit=10)
        assert markets[0]["price"] == 0.5
        assert markets[0]["price_source"] == "gamma_fallback"


class TestBookLevelFallback:
    def test_get_book_levels_uses_raw_when_parsed_empty(self, monkeypatch):
        seren = _mock_seren()
        client = PolymarketClient(seren_client=seren, dry_run=True)

        monkeypatch.setattr(
            "polymarket_live.fetch_book",
            lambda token_id: {
                "bids": [],
                "asks": [],
                "raw": {
                    "bids": [{"price": "0.001", "size": "100"}],
                    "asks": [{"price": "0.999", "size": "100"}],
                },
            },
        )

        best_bid, best_ask = client._get_book_levels("TOKEN-1")
        assert best_bid == 0.001
        assert best_ask == 0.999

    def test_get_midpoint_from_raw_book(self, monkeypatch):
        seren = _mock_seren()
        client = PolymarketClient(seren_client=seren, dry_run=True)

        monkeypatch.setattr(
            "polymarket_live.fetch_book",
            lambda token_id: {
                "bids": [],
                "asks": [],
                "raw": {
                    "bids": [{"price": "0.30", "size": "100"}],
                    "asks": [{"price": "0.70", "size": "100"}],
                },
            },
        )

        mid = client.get_midpoint("TOKEN-1")
        assert mid == pytest.approx(0.50, abs=0.001)

    def test_get_price_buy_returns_ask(self, monkeypatch):
        seren = _mock_seren()
        client = PolymarketClient(seren_client=seren, dry_run=True)

        monkeypatch.setattr(
            "polymarket_live.fetch_book",
            lambda token_id: {
                "bids": [],
                "asks": [],
                "raw": {
                    "bids": [{"price": "0.40", "size": "100"}],
                    "asks": [{"price": "0.60", "size": "100"}],
                },
            },
        )

        assert client.get_price("TOKEN-1", "BUY") == 0.60
        assert client.get_price("TOKEN-1", "SELL") == 0.40
