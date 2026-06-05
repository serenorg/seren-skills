"""Outlook sender preflight (#933, fixed in #935).

`microsoft-outlook` runs a `default_deny` endpoint allowlist with no identity
endpoint (`GET /me` is forbidden). The preflight therefore checks connection
liveness with an *allowed* read endpoint (`/me/mailFolders`) and fails fast on a
missing/expired OAuth connection. It must never report an allowlist
"forbidden endpoint" 403 as an OAuth/setup problem.
"""

from __future__ import annotations

import pytest

from scripts.email_send import OutlookEmailSender
from scripts.proposal import SetupBlocked
from scripts.seren_client import PublisherError


class FoldersGateway:
    def __init__(self, *, result=None, error=None) -> None:
        self._result = {"value": []} if result is None else result
        self._error = error
        self.calls: list[tuple[str, str, str]] = []

    def call_publisher(self, publisher, *, method="GET", path="/", **kwargs):
        self.calls.append((publisher, method, path))
        if self._error is not None:
            raise self._error
        return self._result


def test_preflight_uses_allowed_endpoint_when_connection_live():
    gateway = FoldersGateway(result={"value": [{"id": "inbox"}]})

    OutlookEmailSender(gateway).preflight("taariq@serendb.com")  # no raise

    publisher, method, path = gateway.calls[0]
    assert publisher == "microsoft-outlook"
    assert method == "GET"
    assert path.startswith("/me/mailFolders")  # an allowed endpoint
    assert not path.rstrip("/").endswith("/me")  # never the forbidden identity endpoint


def test_preflight_blocks_when_outlook_oauth_missing():
    gateway = FoldersGateway(error=PublisherError(401, "OAuthRequired: provider 'microsoft'"))

    with pytest.raises(SetupBlocked) as exc:
        OutlookEmailSender(gateway).preflight("taariq@serendb.com")

    assert "taariq@serendb.com" in str(exc.value)


def test_preflight_does_not_mislabel_forbidden_endpoint_as_oauth():
    # Regression for #935: an allowlist "forbidden endpoint" 403 must be
    # re-raised as-is, never converted to a misleading OAuth SetupBlocked.
    error = PublisherError(
        403,
        '{"error":"Forbidden","message":"Forbidden: Endpoint GET me is not in the '
        'allowed endpoints list for this publisher"}',
    )
    gateway = FoldersGateway(error=error)

    with pytest.raises(PublisherError):
        OutlookEmailSender(gateway).preflight("taariq@serendb.com")
