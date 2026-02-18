"""
Unit tests for SerenClient (Coinbase Exchange auth and API methods)
"""

import base64
import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch
import pytest

from seren_client import SerenClient


def make_client():
    """Return a SerenClient with test credentials"""
    secret = base64.b64encode(b'test_secret_bytes').decode()
    return SerenClient(
        seren_api_key='sb_test',
        cb_access_key='key123',
        cb_secret=secret,
        cb_passphrase='passphrase123'
    )


# ========== Auth / Signature ==========

class TestSign:
    def test_signature_is_base64_encoded(self):
        client = make_client()
        sig, ts = client._sign('GET', '/accounts')
        # Must be valid base64
        decoded = base64.b64decode(sig)
        assert len(decoded) == 32  # SHA-256 = 32 bytes

    def test_timestamp_is_numeric_string(self):
        client = make_client()
        _, ts = client._sign('GET', '/accounts')
        float(ts)  # must not raise

    def test_signature_changes_with_method(self):
        client = make_client()
        with patch('time.time', return_value=1700000000.0):
            sig_get, _ = client._sign('GET', '/accounts')
            sig_post, _ = client._sign('POST', '/accounts')
        assert sig_get != sig_post

    def test_signature_changes_with_path(self):
        client = make_client()
        with patch('time.time', return_value=1700000000.0):
            sig_a, _ = client._sign('GET', '/accounts')
            sig_b, _ = client._sign('GET', '/orders')
        assert sig_a != sig_b

    def test_signature_changes_with_body(self):
        client = make_client()
        with patch('time.time', return_value=1700000000.0):
            sig_empty, _ = client._sign('POST', '/orders', '')
            sig_body, _ = client._sign('POST', '/orders', '{"side":"buy"}')
        assert sig_empty != sig_body

    def test_signature_is_deterministic_at_same_timestamp(self):
        client = make_client()
        with patch('time.time', return_value=1700000000.0):
            sig1, ts1 = client._sign('GET', '/accounts')
            sig2, ts2 = client._sign('GET', '/accounts')
        assert sig1 == sig2
        assert ts1 == ts2


# ========== Response Envelope Unwrapping ==========

class TestCallUnwrap:
    def _mock_response(self, payload):
        mock_resp = MagicMock()
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status.return_value = None
        return mock_resp

    def test_unwraps_body_envelope(self):
        client = make_client()
        inner = [{'id': 'acc1', 'currency': 'BTC', 'available': '0.5'}]
        with patch('requests.request', return_value=self._mock_response({'body': inner})):
            result = client._call('GET', '/accounts')
        assert result == inner

    def test_returns_raw_when_no_envelope(self):
        client = make_client()
        raw = [{'id': 'acc1'}]
        with patch('requests.request', return_value=self._mock_response(raw)):
            result = client._call('GET', '/accounts')
        assert result == raw


# ========== get_account_balance ==========

class TestGetAccountBalance:
    def _accounts(self):
        return [
            {'currency': 'BTC', 'available': '0.12345678', 'balance': '0.12345678'},
            {'currency': 'USD', 'available': '987.65', 'balance': '987.65'},
        ]

    def test_returns_btc_balance(self):
        client = make_client()
        with patch.object(client, 'get_accounts', return_value=self._accounts()):
            assert client.get_account_balance('BTC') == pytest.approx(0.12345678)

    def test_returns_usd_balance(self):
        client = make_client()
        with patch.object(client, 'get_accounts', return_value=self._accounts()):
            assert client.get_account_balance('USD') == pytest.approx(987.65)

    def test_returns_zero_for_unknown_currency(self):
        client = make_client()
        with patch.object(client, 'get_accounts', return_value=self._accounts()):
            assert client.get_account_balance('ETH') == 0.0


# ========== validate_product ==========

class TestValidateProduct:
    def _products(self):
        return [
            {'id': 'BTC-USD', 'status': 'online', 'quote_currency': 'USD'},
            {'id': 'ETH-USD', 'status': 'online', 'quote_currency': 'USD'},
            {'id': 'XYZ-USD', 'status': 'offline', 'quote_currency': 'USD'},
        ]

    def test_valid_online_product(self):
        client = make_client()
        with patch.object(client, 'get_products', return_value=self._products()):
            assert client.validate_product('BTC-USD') is True

    def test_offline_product_returns_false(self):
        client = make_client()
        with patch.object(client, 'get_products', return_value=self._products()):
            assert client.validate_product('XYZ-USD') is False

    def test_unknown_product_returns_false(self):
        client = make_client()
        with patch.object(client, 'get_products', return_value=self._products()):
            assert client.validate_product('FAKE-USD') is False


# ========== cancel_all_orders ==========

class TestCancelAllOrders:
    def test_cancels_each_open_order(self):
        client = make_client()
        open_orders = [{'id': 'a'}, {'id': 'b'}, {'id': 'c'}]
        cancelled = []

        with patch.object(client, 'get_open_orders', return_value=open_orders):
            with patch.object(client, 'cancel_order', side_effect=lambda oid: cancelled.append(oid)):
                count = client.cancel_all_orders('BTC-USD')

        assert count == 3
        assert sorted(cancelled) == ['a', 'b', 'c']

    def test_returns_zero_when_no_open_orders(self):
        client = make_client()
        with patch.object(client, 'get_open_orders', return_value=[]):
            count = client.cancel_all_orders('BTC-USD')
        assert count == 0

    def test_continues_on_single_cancel_error(self):
        client = make_client()
        open_orders = [{'id': 'a'}, {'id': 'b'}]
        call_count = [0]

        def cancel(oid):
            call_count[0] += 1
            if oid == 'a':
                raise Exception("network error")

        with patch.object(client, 'get_open_orders', return_value=open_orders):
            with patch.object(client, 'cancel_order', side_effect=cancel):
                count = client.cancel_all_orders('BTC-USD')

        assert count == 1  # only 'b' succeeded
