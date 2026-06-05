"""Sender-identity preflight for the Seren-tenant mailbox fallback (#933).

Until MS Publisher Verification completes, the skill sends from a fixed
Seren-tenant mailbox in both dry-run and live. `OutlookEmailSender.preflight`
asserts the OAuth-connected `microsoft-outlook` mailbox matches the configured
sender before any send, so the run fails fast (and never sends from the wrong
or customer mailbox).
"""

from __future__ import annotations

import pytest

from scripts.email_send import OutlookEmailSender
from scripts.proposal import SetupBlocked
from scripts.seren_client import PublisherError


class MeGateway:
    def __init__(self, *, me=None, error=None) -> None:
        self._me = me
        self._error = error
        self.calls: list[tuple[str, str, str]] = []

    def call_publisher(self, publisher, *, method="GET", path="/", **kwargs):
        self.calls.append((publisher, method, path))
        if self._error is not None:
            raise self._error
        return self._me


def test_preflight_accepts_matching_connected_sender():
    gateway = MeGateway(me={"mail": "Taariq@SerenDB.com"})

    OutlookEmailSender(gateway).preflight("taariq@serendb.com")  # case-insensitive

    assert ("microsoft-outlook", "GET", "/me") in gateway.calls


def test_preflight_blocks_when_connected_sender_differs():
    gateway = MeGateway(me={"userPrincipalName": "cristin@glide.com"})

    with pytest.raises(SetupBlocked) as exc:
        OutlookEmailSender(gateway).preflight("taariq@serendb.com")

    message = str(exc.value)
    assert "cristin@glide.com" in message
    assert "taariq@serendb.com" in message


def test_preflight_blocks_when_outlook_oauth_missing():
    gateway = MeGateway(error=PublisherError(403, "OAuthRequired: provider 'microsoft'"))

    with pytest.raises(SetupBlocked):
        OutlookEmailSender(gateway).preflight("taariq@serendb.com")
