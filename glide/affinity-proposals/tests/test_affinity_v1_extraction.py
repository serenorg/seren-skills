from __future__ import annotations

import base64

from scripts.affinity import (
    AffinityClient,
    _extract_status,
    _field_name_map,
)


def _auth_header(client: AffinityClient) -> str:
    return client.client.headers["Authorization"]


def test_basic_auth_sends_key_as_password_and_strips():
    # Affinity v1 wants the key in the PASSWORD slot (empty username), and
    # the stored key may carry stray whitespace.
    client = AffinityClient("  my-secret-key  ")
    expected = "Basic " + base64.b64encode(b":my-secret-key").decode()
    assert _auth_header(client) == expected


def test_field_name_map_from_list_schema():
    schema = {
        "fields": [
            {"id": 5570062, "name": "Status"},
            {"id": 5570064, "name": "Owners"},
        ]
    }
    assert _field_name_map(schema) == {5570062: "Status", 5570064: "Owners"}


def test_extract_status_uses_field_id_when_name_absent():
    # Real v1 field-values: no field_name, status value is a {text} dict.
    field_values = [
        {"id": 21179930121, "field_id": 5570062, "value": {"text": "Engaged - 25%"}},
        {"id": 21179930123, "field_id": 5570064, "value": 194941263},
    ]
    names = {5570062: "Status", 5570064: "Owners"}
    status, fv_id = _extract_status(field_values, names)
    assert status == "Engaged - 25%"
    assert fv_id == "21179930121"


def test_extract_status_still_honors_explicit_field_name():
    field_values = [{"id": 9, "field_name": "Stage", "value": "Engaged - 25%"}]
    status, fv_id = _extract_status(field_values)
    assert status == "Engaged - 25%"
    assert fv_id == "9"


def test_notes_unwraps_paginated_envelope_and_follows_pages():
    client = AffinityClient("k")
    pages = [
        {"notes": [{"id": 1, "content": "a"}], "next_page_token": "T2"},
        {"notes": [{"id": 2, "content": "b"}], "next_page_token": None},
    ]
    seen_params = []

    def fake_request(method, path, **kwargs):
        seen_params.append(kwargs.get("params"))
        return pages[len(seen_params) - 1]

    client._request = fake_request  # type: ignore[method-assign]
    notes = client.notes(291287983)
    assert [n["id"] for n in notes] == [1, 2]
    assert seen_params[1].get("page_token") == "T2"


def test_notes_handles_legacy_bare_array():
    client = AffinityClient("k")
    client._request = lambda *a, **k: [{"id": 1, "content": "a"}]  # type: ignore[method-assign]
    assert client.notes(1) == [{"id": 1, "content": "a"}]
