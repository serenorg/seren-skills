"""Critical-only tests for the auto-onboarding + AGENTACCESS bind.

Four assertions, all load-bearing:

  1. fill_onboarding_form fills username, ticks the unchecked
     attestation, and clicks Continue (zero-touch onboarding).
  2. collision_fallback returns base + sha256(email)[:4] suffix and
     stays under the max length cap.
  3. bind_agentaccess sends the exact submitReferralCode mutation with
     code='AGENTACCESS' and the Cookie auth header.
  4. bind_agentaccess swallows 'already redeemed' errors so re-runs
     after a successful first bind don't fail closed.

Other behaviors (collision retry inside _drive_onboarding_if_present,
URL polling, forwarder errors) are exercised transitively or by the
Phase-14 live test; not pinned here per the critical-only rule.
"""

from __future__ import annotations

import pytest

from otp_worker.playwright_client import (
    SEL_ONBOARDING_CONTINUE,
    SEL_ONBOARDING_GEO_ATTESTATION,
    SEL_ONBOARDING_USERNAME,
    fill_onboarding_form,
)
from otp_worker.username import (
    base_username_from_email,
    collision_fallback,
)
from prophet.affiliate import (
    AGENTACCESS_REFERRAL_CODE,
    bind_agentaccess,
)


# ---------------------------------------------------------------------------
# helpers


class StubBrowserSession:
    """Minimal BrowserSession stand-in that records every action.

    Tests inspect `actions` to assert the form was driven correctly.
    `checked` controls what is_checked() returns for the geo-attestation
    selector — flipping it lets us cover both "needs ticking" and
    "already ticked" paths from a single shape.
    """

    def __init__(self, *, geo_already_checked: bool = False) -> None:
        self.actions: list[tuple[str, ...]] = []
        self._geo_checked = geo_already_checked

    def navigate(self, url: str) -> None:
        self.actions.append(("navigate", url))

    def click(self, selector: str) -> None:
        self.actions.append(("click", selector))
        if selector == SEL_ONBOARDING_GEO_ATTESTATION:
            self._geo_checked = True

    def fill(self, selector: str, value: str) -> None:
        self.actions.append(("fill", selector, value))

    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None:
        self.actions.append(("wait_for", selector))

    def get_local_storage(self, key: str) -> str | None:  # pragma: no cover
        return None

    def get_cookie(self, name: str) -> str | None:  # pragma: no cover
        return None

    def get_url(self) -> str:  # pragma: no cover
        return "https://app.prophetmarket.ai/"

    def is_checked(self, selector: str) -> bool:
        return self._geo_checked if selector == SEL_ONBOARDING_GEO_ATTESTATION else False


class StubGateway:
    """Captures every gateway.call invocation and returns canned payloads."""

    def __init__(self, response: dict | None = None) -> None:
        self.calls: list[dict] = []
        self._response = response or {"data": {"submitReferralCode": {"__typename": "Ok"}}}

    def call(
        self,
        publisher: str,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict | None = None,
    ) -> dict:
        self.calls.append(
            {"publisher": publisher, "method": method, "path": path,
             "body": body, "headers": headers}
        )
        return self._response


# ---------------------------------------------------------------------------
# Test 1: fill_onboarding_form drives all three actions when geo unchecked


def test_fill_onboarding_form_fills_ticks_and_continues() -> None:
    session = StubBrowserSession(geo_already_checked=False)

    fill_onboarding_form(session, username="taariq")

    fills = [a for a in session.actions if a[0] == "fill"]
    clicks = [a for a in session.actions if a[0] == "click"]
    assert ("fill", SEL_ONBOARDING_USERNAME, "taariq") in fills
    assert ("click", SEL_ONBOARDING_GEO_ATTESTATION) in clicks
    assert ("click", SEL_ONBOARDING_CONTINUE) in clicks
    # Tick must precede Continue, otherwise the button stays disabled.
    geo_idx = clicks.index(("click", SEL_ONBOARDING_GEO_ATTESTATION))
    cont_idx = clicks.index(("click", SEL_ONBOARDING_CONTINUE))
    assert geo_idx < cont_idx


# ---------------------------------------------------------------------------
# Test 2: collision_fallback shape + bound


def test_collision_fallback_appends_4char_hash_suffix() -> None:
    email = "taariq@serendb.com"

    base = base_username_from_email(email)
    fallback = collision_fallback(email)

    # Deterministic: base="taariq", sha256("taariq@serendb.com")[:4] = a
    # 4-char hex prefix. Repeated calls return the same string.
    assert base == "taariq"
    assert fallback.startswith("taariq_")
    assert len(fallback) - len("taariq_") == 4
    assert fallback == collision_fallback(email)
    # Never exceeds the 30-char cap, even with very long local-parts.
    long_email = ("x" * 50) + "@example.com"
    assert len(collision_fallback(long_email)) <= 30


# ---------------------------------------------------------------------------
# Test 3: bind_agentaccess sends the exact submitReferralCode mutation


def test_bind_agentaccess_submits_correct_mutation_and_auth() -> None:
    gateway = StubGateway()
    jwt = "eyJ.fake.jwt"

    bind_agentaccess(gateway=gateway, jwt=jwt)

    assert len(gateway.calls) == 1
    call = gateway.calls[0]
    assert call["publisher"] == "prophet-ai"
    assert call["method"] == "POST"
    assert call["path"] == "/api/graphql"
    assert call["headers"] == {"Cookie": f"privy-token={jwt}"}
    body = call["body"] or {}
    assert "submitReferralCode" in (body.get("query") or "")
    assert (body.get("variables") or {}) == {"code": AGENTACCESS_REFERRAL_CODE}


# ---------------------------------------------------------------------------
# Test 4: bind_agentaccess swallows "already redeemed" idempotency error


def test_bind_agentaccess_swallows_already_redeemed_error() -> None:
    gateway = StubGateway(
        response={"errors": [{"message": "Referral code already redeemed by user"}]}
    )

    # Must NOT raise — this is the post-first-bind re-run case.
    bind_agentaccess(gateway=gateway, jwt="eyJ.fake.jwt")

    assert len(gateway.calls) == 1
