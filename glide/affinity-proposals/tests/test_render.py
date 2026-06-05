from __future__ import annotations

import pytest

from scripts.proposal import SetupBlocked, SharePointRenderer
from scripts.seren_client import PublisherError


class OAuthMissingGateway:
    def call_publisher(self, publisher, *, method="GET", path="/", body=None,
                       data=None, content_type=None, headers=None, response_format="json"):
        raise PublisherError(403, "OAuthRequired: provider 'microsoft'")


def test_sharepoint_preflight_surfaces_missing_oauth_as_setup_blocker():
    with pytest.raises(SetupBlocked) as exc:
        SharePointRenderer(OAuthMissingGateway()).preflight()
    assert "Microsoft OAuth connection required" in str(exc.value)


class RecordingGraphGateway:
    """Fake microsoft-sharepoint Graph publisher. The folder POST returns
    409 (already exists) to exercise the tolerate-existing-folder path."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.uploaded: bytes | None = None

    def call_publisher(self, publisher, *, method="GET", path="/", body=None,
                       data=None, content_type=None, headers=None, response_format="json"):
        assert publisher == "microsoft-sharepoint"
        self.calls.append((method, path))
        if method == "GET" and path == "/sites/root":
            return {"id": "site-1"}
        if method == "GET" and path == "/sites/site-1/drive":
            return {"id": "drive-1"}
        if method == "POST" and path == "/drives/drive-1/root/children":
            raise PublisherError(409, "nameAlreadyExists")
        if method == "PUT" and path.startswith("/drives/drive-1/root:/"):
            assert data is not None, "upload must send raw bytes, not JSON"
            self.uploaded = data
            return {"id": "item-1"}
        if method == "GET" and path == "/drives/drive-1/items/item-1/content?format=pdf":
            # GatewayClient decodes the gateway's base64 binary envelope and
            # returns raw bytes to render_pdf (seren-core #182).
            assert response_format == "binary"
            return b"%PDF-1.7\nrendered"
        raise AssertionError(f"unexpected {method} {path}")


def test_render_pdf_uploads_by_path_and_downloads_as_pdf(tmp_path):
    pptx = tmp_path / "Acme_proposal.pptx"
    pptx.write_bytes(b"PPTX-BYTES")
    gateway = RecordingGraphGateway()

    out = SharePointRenderer(gateway, folder_name="AI Proposals").render_pdf(pptx)

    assert out.startswith(b"%PDF")
    assert gateway.uploaded == b"PPTX-BYTES"  # raw bytes uploaded
    paths = [p for _, p in gateway.calls]
    assert "/sites/root" in paths
    assert "/sites/site-1/drive" in paths
    assert any(
        p.startswith("/drives/drive-1/root:/AI%20Proposals/Acme_proposal.pptx:/content")
        for p in paths
    )
    assert "/drives/drive-1/items/item-1/content?format=pdf" in paths
    assert not any("/tools/" in p for p in paths)  # no MCP tool-name routes


def test_render_pdf_rejects_non_pdf_bytes(tmp_path):
    pptx = tmp_path / "x.pptx"
    pptx.write_bytes(b"PPTX")

    class NotPdfGateway(RecordingGraphGateway):
        def call_publisher(self, publisher, *, method="GET", path="/", **kw):
            if method == "GET" and path == "/drives/drive-1/items/item-1/content?format=pdf":
                return b"<html>error</html>"
            return super().call_publisher(publisher, method=method, path=path, **kw)

    with pytest.raises(RuntimeError):
        SharePointRenderer(NotPdfGateway(), folder_name="AI Proposals").render_pdf(pptx)


# The post-#182 gateway returns binary downloads as recoverable base64 in its
# JSON envelope; the skill decodes them (GatewayClient.response_format="binary").
# End-to-end decode against the real envelope shape is covered in
# test_render_binary.py.
