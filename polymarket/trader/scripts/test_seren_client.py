"""
Unit tests for SerenClient._extract_text response shape handling.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import patch

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
