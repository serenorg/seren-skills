"""Issue #518: Prophet wallet-only accounts have no email — bind on id alone.

The user's Privy JWT for a wallet-only Prophet account
(`viewer { user { id email } }` returns `id` set, `email` empty)
must produce a successful viewer bind. The bounty reconciler
attributes earnings by `creator.id` (= viewer_id), not by email —
the email check was a legacy leftover from when Privy auth was
email-OTP-only.

Two code paths share the same bug class:
  - `otp_worker.token_acquirer._query_viewer`
  - `prophet.client.MinimalProphetClient.viewer`

Both must accept the wallet-only payload; one focused test per
code path locks the contract without duplicating coverage.
"""

from __future__ import annotations

import pytest

from otp_worker import PrivyAuthFailed
from otp_worker.token_acquirer import _query_viewer
from prophet import ProphetSchemaError
from prophet.client import MinimalProphetClient

WALLET_ONLY_VIEWER_PAYLOAD = {
    "data": {
        "viewer": {
            "user": {
                "id": "56b53624-aaaa-bbbb-cccc-ddddeeeeffff",
                "email": "",
            },
            "walletBalance": {
                "availableCents": 0,
                "totalCents": 0,
                "onChainUsdc": 0,
                "safeAddress": "0x48Bc0000000000000000000000000000000000Bc",
                "safeDeployed": True,
            },
        }
    }
}


def test_query_viewer_accepts_wallet_only_account_with_empty_email(stub_transport) -> None:
    stub_transport.register_default(WALLET_ONLY_VIEWER_PAYLOAD)

    viewer_id, viewer_email = _query_viewer(transport=stub_transport, jwt="eyJ-wallet")

    assert viewer_id == "56b53624-aaaa-bbbb-cccc-ddddeeeeffff"
    assert viewer_email == ""


def test_query_viewer_still_fails_closed_when_viewer_id_is_missing(stub_transport) -> None:
    stub_transport.register_default(
        {"data": {"viewer": {"user": {"id": "", "email": ""}}}}
    )
    with pytest.raises(PrivyAuthFailed, match="viewer payload empty"):
        _query_viewer(transport=stub_transport, jwt="eyJ-bad")


def test_minimal_prophet_client_viewer_accepts_wallet_only_payload(stub_transport) -> None:
    stub_transport.register("Viewer", WALLET_ONLY_VIEWER_PAYLOAD)
    client = MinimalProphetClient(transport=stub_transport)

    identity = client.viewer(jwt="eyJ-wallet")

    assert identity.id == "56b53624-aaaa-bbbb-cccc-ddddeeeeffff"
    assert identity.email == ""


def test_minimal_prophet_client_viewer_still_fails_closed_when_id_missing(stub_transport) -> None:
    stub_transport.register(
        "Viewer",
        {"data": {"viewer": {"user": {"id": "", "email": ""}}}},
    )
    client = MinimalProphetClient(transport=stub_transport)
    with pytest.raises(ProphetSchemaError, match="incomplete payload"):
        client.viewer(jwt="eyJ-bad")
