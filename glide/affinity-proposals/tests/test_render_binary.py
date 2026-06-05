"""Render-leg tests against the real post-#182 gateway envelope.

The gateway always wraps publisher responses in a JSON envelope
(`{"data": {...}}`). For a binary download (SharePoint `?format=pdf`) the
deployed gateway (seren-core 38f448eb) returns:

    {"data": {"status": 200, "body": null,
              "body_base64": "<base64 of raw PDF bytes>",
              "content_type": "application/pdf",
              "response_bytes": <int>}}

These tests exercise the real `GatewayClient` transport and the real
`SharePointRenderer.render_pdf` path with only the network boundary
(`urllib.request.urlopen`) mocked — i.e. in-flow, not a reimplementation.
"""

from __future__ import annotations

import base64
import json
import urllib.request

from scripts.proposal import SharePointRenderer
from scripts.seren_client import GatewayClient


class _Resp:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _envelope(body: object) -> _Resp:
    return _Resp(json.dumps({"data": {"status": 200, "body": body}}).encode("utf-8"))


def _make_fake_urlopen(pdf: bytes):
    b64 = base64.b64encode(pdf).decode("ascii")

    def fake_urlopen(request, *args, **kwargs):
        url = request.full_url
        method = request.get_method()
        if method == "GET" and url.endswith("/sites/root"):
            return _envelope({"id": "site-1"})
        if method == "GET" and url.endswith("/sites/site-1/drive"):
            return _envelope({"id": "drive-1"})
        if method == "POST" and url.endswith("/drives/drive-1/root/children"):
            return _envelope({"id": "folder-1"})
        if method == "PUT" and "/root:/" in url and url.endswith(":/content"):
            return _envelope({"id": "item-1"})
        if method == "GET" and "/items/item-1/content?format=pdf" in url:
            envelope = {
                "data": {
                    "status": 200,
                    "body": None,
                    "body_base64": b64,
                    "content_type": "application/pdf",
                    "response_bytes": len(pdf),
                }
            }
            return _Resp(json.dumps(envelope).encode("utf-8"))
        raise AssertionError(f"unexpected {method} {url}")

    return fake_urlopen


def test_render_pdf_decodes_body_base64_envelope_end_to_end(tmp_path, monkeypatch):
    pdf = b"%PDF-1.7\n" + b"\x00\x01\x02\xff\xfe" * 40 + b"\nstartxref\n0\n%%EOF"
    monkeypatch.setattr(urllib.request, "urlopen", _make_fake_urlopen(pdf))

    pptx = tmp_path / "Acme_proposal.pptx"
    pptx.write_bytes(b"PPTX-BYTES")

    out = SharePointRenderer(GatewayClient("test-key"), folder_name="AI Proposals").render_pdf(pptx)

    assert out == pdf  # exact byte round-trip, no U+FFFD corruption
    assert out.startswith(b"%PDF")
    assert out.rstrip().endswith(b"%%EOF")
    assert len(out) == len(pdf)


def test_call_publisher_binary_decodes_body_base64(monkeypatch):
    pdf = b"%PDF-1.7\nbinary\xff\xfe\n%%EOF"
    b64 = base64.b64encode(pdf).decode("ascii")
    envelope = json.dumps(
        {
            "data": {
                "status": 200,
                "body": None,
                "body_base64": b64,
                "content_type": "application/pdf",
                "response_bytes": len(pdf),
            }
        }
    ).encode("utf-8")
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, *a, **k: _Resp(envelope))

    out = GatewayClient("k").call_publisher(
        "microsoft-sharepoint",
        method="GET",
        path="/drives/d/items/i/content?format=pdf",
        response_format="binary",
    )
    assert out == pdf


def test_call_publisher_binary_falls_back_to_text_body(monkeypatch):
    envelope = json.dumps({"data": {"status": 200, "body": "plain text body"}}).encode("utf-8")
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, *a, **k: _Resp(envelope))

    out = GatewayClient("k").call_publisher("p", response_format="binary")
    assert out == b"plain text body"
