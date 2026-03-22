"""
Unit tests for SerenClient._extract_text response shape handling.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))

# Patch out requests.Session so __init__ doesn't need a real API key
with patch('requests.Session'):
    from seren_client import SerenClient

CLIENT = SerenClient.__new__(SerenClient)


class TestExtractText:
    # ------------------------------------------------------------------
    # OpenAI-style choices[].message.content — string
    # ------------------------------------------------------------------
    def test_openai_string_content(self):
        response = {
            'choices': [{'message': {'content': 'hello world'}}]
        }
        assert CLIENT._extract_text(response) == 'hello world'

    # ------------------------------------------------------------------
    # OpenAI-style choices[].message.content — content-block array
    # ------------------------------------------------------------------
    def test_openai_content_block_array(self):
        response = {
            'choices': [{'message': {'content': [
                {'type': 'text', 'text': 'block text'},
                {'type': 'image', 'url': 'http://example.com/img.png'},
            ]}}]
        }
        assert CLIENT._extract_text(response) == 'block text'

    # ------------------------------------------------------------------
    # Wrapped in 'body' envelope
    # ------------------------------------------------------------------
    def test_body_wrapped_response(self):
        response = {
            'body': {
                'choices': [{'message': {'content': 'wrapped text'}}]
            }
        }
        assert CLIENT._extract_text(response) == 'wrapped text'

    # ------------------------------------------------------------------
    # Responses-API-style output[].content[].text
    # ------------------------------------------------------------------
    def test_responses_api_style(self):
        response = {
            'output': [
                {'content': [
                    {'type': 'text', 'text': 'responses api text'},
                ]}
            ]
        }
        assert CLIENT._extract_text(response) == 'responses api text'

    # ------------------------------------------------------------------
    # Plain top-level text field
    # ------------------------------------------------------------------
    def test_plain_text_field(self):
        response = {'text': 'plain text fallback'}
        assert CLIENT._extract_text(response) == 'plain text fallback'

    # ------------------------------------------------------------------
    # Error payload — must raise with informative message
    # ------------------------------------------------------------------
    def test_error_payload_raises(self):
        response = {'error': 'model overloaded'}
        with pytest.raises(ValueError, match=r"Unsupported model response shape"):
            CLIENT._extract_text(response)

    def test_error_payload_lists_keys(self):
        response = {'error': 'something went wrong', 'status': 500}
        with pytest.raises(ValueError, match=r"\['error', 'status'\]"):
            CLIENT._extract_text(response)

    # ------------------------------------------------------------------
    # Completely unknown shape
    # ------------------------------------------------------------------
    def test_unknown_shape_raises(self):
        response = {'result': 'some unexpected key'}
        with pytest.raises(ValueError, match=r"Unsupported model response shape"):
            CLIENT._extract_text(response)


class TestSerenClientApiKeyResolution:
    def test_explicit_api_key_wins(self):
        with patch.dict('os.environ', {}, clear=True), patch(
            'seren_client.requests.Session'
        ):
            client = SerenClient(api_key='explicit-key')
            assert client.api_key == 'explicit-key'

    def test_uses_seren_api_key_env(self):
        with patch.dict('os.environ', {'SEREN_API_KEY': 'seren-key'}, clear=True), patch(
            'seren_client.requests.Session'
        ):
            client = SerenClient()
            assert client.api_key == 'seren-key'

    def test_falls_back_to_api_key_env(self):
        with patch.dict('os.environ', {'API_KEY': 'desktop-key'}, clear=True), patch(
            'seren_client.requests.Session'
        ):
            client = SerenClient()
            assert client.api_key == 'desktop-key'

    def test_raises_when_no_key_available(self):
        with patch.dict('os.environ', {}, clear=True), patch(
            'seren_client.requests.Session'
        ):
            with pytest.raises(ValueError, match=r"SEREN_API_KEY.*API_KEY"):
                SerenClient()


class TestSerenClientWalletBalance:
    def test_get_wallet_balance_unwraps_data_and_parses_currency_strings(self):
        session = Mock()
        response = Mock()
        response.json.return_value = {
            'data': {
                'balance_atomic': 21028699,
                'balance_usd': '$21.03',
                'funded_balance_usd': '$20.33',
                'promotional_balance_usd': '$0.70',
            }
        }
        response.raise_for_status.return_value = None
        session.get.return_value = response

        with patch.dict('os.environ', {'SEREN_API_KEY': 'seren-key'}, clear=True), patch(
            'seren_client.requests.Session',
            return_value=session,
        ):
            client = SerenClient()

        wallet = client.get_wallet_balance()

        assert wallet['balance_atomic'] == 21028699
        assert wallet['balance_usd'] == 21.03
        assert wallet['funded_balance_usd'] == 20.33
        assert wallet['promotional_balance_usd'] == 0.70
        session.get.assert_called_once_with(
            'https://api.serendb.com/wallet/balance',
            timeout=30,
        )

    def test_get_wallet_balance_keeps_top_level_numeric_response(self):
        session = Mock()
        response = Mock()
        response.json.return_value = {
            'balance_atomic': 1230000,
            'balance_usd': 1.23,
        }
        response.raise_for_status.return_value = None
        session.get.return_value = response

        with patch.dict('os.environ', {'SEREN_API_KEY': 'seren-key'}, clear=True), patch(
            'seren_client.requests.Session',
            return_value=session,
        ):
            client = SerenClient()

        wallet = client.get_wallet_balance()

        assert wallet['balance_atomic'] == 1230000
        assert wallet['balance_usd'] == 1.23
