"""
Unit tests for PolymarketClient auth mode and trading publisher fallback behavior.
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


class TestPolymarketClientAuthMode:
    def test_defaults_to_desktop_mode_without_poly_credentials(self):
        seren = _mock_seren()
        client = PolymarketClient(
            seren_client=seren,
            poly_api_key=None,
            poly_passphrase=None,
            poly_secret=None,
            poly_address=None,
        )
        assert client.desktop_publisher_auth is True
        assert client.auth_mode == 'desktop_publisher_auth'
        assert client._get_auth_headers() == {}

    def test_legacy_header_mode_when_credentials_present(self):
        seren = _mock_seren()
        client = PolymarketClient(
            seren_client=seren,
            poly_api_key='k',
            poly_passphrase='p',
            poly_secret='s',
            poly_address='0xabc',
        )
        assert client.desktop_publisher_auth is False
        assert client.auth_mode == 'direct_polymarket_headers'
        assert client._get_auth_headers()['POLY_API_KEY'] == 'k'

    def test_forced_legacy_mode_requires_credentials(self):
        seren = _mock_seren()
        with pytest.raises(ValueError, match=r"Polymarket credentials required"):
            PolymarketClient(
                seren_client=seren,
                desktop_publisher_auth=False,
            )


class TestPolymarketClientTradingFallback:
    def test_falls_back_to_legacy_slug_on_404(self):
        seren = _mock_seren()
        seren.call_publisher.side_effect = [
            Exception("Publisher call failed: 404 - not found"),
            {'data': []},
        ]
        client = PolymarketClient(seren_client=seren)

        result = client._call_trading(method='GET', path='/positions')
        assert result == {'data': []}
        assert seren.call_publisher.call_count == 2
        first_call = seren.call_publisher.call_args_list[0].kwargs
        second_call = seren.call_publisher.call_args_list[1].kwargs
        assert first_call['publisher'] == 'polymarket-trading'
        assert second_call['publisher'] == 'polymarket-trading-serenai'

    def test_desktop_auth_unauthorized_raises_helpful_error(self):
        seren = _mock_seren()
        seren.call_publisher.side_effect = Exception("Publisher call failed: 401 - unauthorized")
        client = PolymarketClient(seren_client=seren)

        with pytest.raises(Exception, match=r"desktop publisher authentication failed"):
            client._call_trading(method='GET', path='/positions')
