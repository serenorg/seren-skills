from __future__ import annotations

import pytest

from scripts.seren_client import _loads_model_json


def test_parses_fenced_json_block():
    assert _loads_model_json('```json\n{"ok": true}\n```') == {"ok": True}


def test_parses_bare_fence_without_language_tag():
    assert _loads_model_json('```\n{"a": 1}\n```') == {"a": 1}


def test_parses_plain_json():
    assert _loads_model_json('{"a": 1, "b": [2, 3]}') == {"a": 1, "b": [2, 3]}


def test_extracts_object_from_surrounding_prose():
    content = 'Here is the profile you asked for:\n{"structure": "offshore"} — done.'
    assert _loads_model_json(content) == {"structure": "offshore"}


def test_brace_inside_string_does_not_truncate_object():
    assert _loads_model_json('{"note": "a } brace in text", "n": 1}') == {
        "note": "a } brace in text",
        "n": 1,
    }


def test_raises_when_no_json_object_present():
    with pytest.raises(RuntimeError):
        _loads_model_json("the model refused and returned only prose")
