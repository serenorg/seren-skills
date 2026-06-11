from __future__ import annotations

import urllib.request

import pytest

from scripts.seren_client import GatewayClient, PublisherError


class _Resp:
    status = 200
    content = b""

    def __init__(self, payload: bytes = b"{}") -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_gateway_client_passes_configured_timeout_to_urlopen(monkeypatch) -> None:
    seen: dict[str, float] = {}

    def fake_urlopen(request, *, timeout):
        seen["timeout"] = timeout
        return _Resp(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert GatewayClient("k", timeout=12.5).call_publisher("seren-models") == {
        "ok": True
    }
    assert seen["timeout"] == 12.5


def test_gateway_client_turns_timeout_into_clear_publisher_error(monkeypatch) -> None:
    def fake_urlopen(request, *, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(PublisherError) as exc:
        GatewayClient("k", timeout=3).call_publisher("seren-models")

    assert exc.value.status == 0
    assert "timed out after 3s" in str(exc.value)
    assert "seren-models" in str(exc.value)
