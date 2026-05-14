from __future__ import annotations

from datetime import datetime, timezone

import pytest

from otp_worker import PrivyAuthFailed
from otp_worker.token_acquirer import acquire_token
from prophet import ProphetGraphQLError


class StaticInboxReader:
    def read_latest_otp_email(self, *, sender_filter: str, since: datetime) -> str:
        return "Your verification code is 123456."


class StaticBrowserSession:
    def __init__(self) -> None:
        self.filled: list[tuple[str, str]] = []

    def navigate(self, url: str) -> None:
        pass

    def click(self, selector: str) -> None:
        pass

    def fill(self, selector: str, value: str) -> None:
        self.filled.append((selector, value))

    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None:
        pass

    def get_local_storage(self, key: str) -> str | None:
        return "eyJ.test.jwt"

    def get_cookie(self, name: str) -> str | None:
        return "cookie-value"

    def get_url(self) -> str:
        return "https://app.prophetmarket.ai/"

    def is_checked(self, selector: str) -> bool:
        return True


def _now_factory() -> tuple[datetime, callable]:
    current = datetime(2026, 5, 13, 12, 0, 0, tzinfo=timezone.utc)

    def now() -> datetime:
        return current

    return current, now


def test_acquire_token_submits_agentaccess_referral_code(stub_gateway, stub_transport) -> None:
    _, now = _now_factory()
    stub_transport.register(
        "Viewer",
        {
            "data": {
                "viewer": {
                    "user": {"id": "viewer-1", "email": "operator@example.com"},
                    "walletBalance": {"availableCents": 0, "totalCents": 0},
                }
            }
        },
    )
    stub_transport.register(
        "SubmitReferralCode",
        {"data": {"submitReferralCode": {"__typename": "Referral"}}},
    )

    result = acquire_token(
        email="operator@example.com",
        provider="gmail",
        seren_user_id="user-1",
        bounty_id="bounty-1",
        browser_session=StaticBrowserSession(),
        gateway=stub_gateway,
        transport=stub_transport,
        inbox_reader=StaticInboxReader(),
        sleep=lambda _seconds: None,
        now=now,
    )

    assert result.prophet_viewer_id == "viewer-1"
    referral_calls = [
        call for call in stub_transport.calls if call["operation_name"] == "SubmitReferralCode"
    ]
    assert referral_calls
    assert referral_calls[0]["variables"] == {"code": "AGENTACCESS"}


def test_acquire_token_fails_closed_when_agentaccess_bind_fails(
    stub_gateway,
    stub_transport,
) -> None:
    _, now = _now_factory()
    stub_transport.register(
        "Viewer",
        {
            "data": {
                "viewer": {
                    "user": {"id": "viewer-1", "email": "operator@example.com"},
                    "walletBalance": {"availableCents": 0, "totalCents": 0},
                }
            }
        },
    )
    stub_transport.register(
        "SubmitReferralCode",
        ProphetGraphQLError("submitReferralCode rejected"),
    )

    with pytest.raises(PrivyAuthFailed, match="referral bind failed"):
        acquire_token(
            email="operator@example.com",
            provider="gmail",
            seren_user_id="user-1",
            bounty_id="bounty-1",
            browser_session=StaticBrowserSession(),
            gateway=stub_gateway,
            transport=stub_transport,
            inbox_reader=StaticInboxReader(),
            sleep=lambda _seconds: None,
            now=now,
        )
