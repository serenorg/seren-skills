from __future__ import annotations

import pytest

from scripts.proposal import SetupBlocked, SharePointRenderer
from scripts.seren_client import PublisherError


class OAuthMissingGateway:
    def call_tool(self, publisher, tool, tool_args=None):
        raise PublisherError(403, "OAuthRequired: provider 'microsoft'")


def test_sharepoint_preflight_surfaces_missing_oauth_as_setup_blocker():
    renderer = SharePointRenderer(OAuthMissingGateway())

    with pytest.raises(SetupBlocked) as exc:
        renderer.preflight()

    assert "Microsoft OAuth connection required" in str(exc.value)
