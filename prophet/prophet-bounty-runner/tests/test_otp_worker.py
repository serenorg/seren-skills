"""Critical-only OTP-worker tests.

Reduced from plan §11.5 (7 test files) to 6 load-bearing assertions
focused on fail-closed and security boundaries:

  1. otp_extractor returns the right 6 digits on a Privy-shaped body
  2. otp_extractor RAISES (not returns None) when no code is present
  3. session_cache writes the file with mode 0600 (security)
  4. session_cache treats corrupted JSON as needs_otp (fail-closed)
  5. token_refresher 401 flips cache to needs_otp WITHOUT raising
  6. auth_facade falls through to TokenAcquirer when cache=needs_otp

Skipped: pagination tests, inbox-reader stubs, playwright client (plan
§11.5 already excludes that one), call-order assertions, backoff counter
tests. Those are exercised in Phase 14 live acceptance.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from otp_worker import OtpCodeNotFound  # noqa: E402
from otp_worker.auth_facade import AuthFacade  # noqa: E402
from otp_worker.otp_extractor import extract_otp_code  # noqa: E402
from otp_worker.session_cache import SessionCache, SessionCacheEntry  # noqa: E402
from otp_worker.token_acquirer import AcquiredSession  # noqa: E402
from otp_worker.token_refresher import RefreshResult, refresh_once  # noqa: E402


# ---------------------------------------------------------------------------
# Test 1 + 2: otp_extractor


def test_otp_extractor_extracts_six_digit_code() -> None:
    body = (
        "Hi taariq,\n\nYour Privy login code is:\n\n482917\n\n"
        "This code expires in 10 minutes.\n"
    )

    code = extract_otp_code(body)

    assert code == "482917"


def test_otp_extractor_raises_when_no_code() -> None:
    body = "Hi taariq, your login was successful. Order #12345-67. Thanks."

    with pytest.raises(OtpCodeNotFound):
        extract_otp_code(body)


# ---------------------------------------------------------------------------
# Test 3 + 4: session_cache


def test_session_cache_writes_with_0600_permissions(tmp_path: Path) -> None:
    cache = SessionCache(path=tmp_path / "privy_session.json")
    entry = SessionCacheEntry(
        user_email="implementer@example.com",
        jwt="eyJ.fake.jwt",
        jwt_expires_at="2026-05-07T13:00:00+00:00",
        refresh_token="rt_fixture",
        state="fresh",
    )

    cache.write(entry)

    file_mode = stat.S_IMODE(os.stat(cache.path).st_mode)
    assert file_mode == 0o600


def test_session_cache_corrupted_file_treated_as_needs_otp(tmp_path: Path) -> None:
    cache_path = tmp_path / "privy_session.json"
    cache_path.write_text("{not valid json", encoding="utf-8")
    cache = SessionCache(path=cache_path)

    entry = cache.read()

    assert entry.state == "needs_otp"


# ---------------------------------------------------------------------------
# Test 5: token_refresher 401 → needs_otp


class _StubHttp401:
    def post_refresh(self, *, url, refresh_token, session_cookie):
        return 401, {"error": "session_revoked"}


def test_token_refresher_401_flips_state_to_needs_otp(tmp_path: Path) -> None:
    cache = SessionCache(path=tmp_path / "privy_session.json")
    cache.write(
        SessionCacheEntry(
            user_email="implementer@example.com",
            jwt="eyJ.expired.jwt",
            refresh_token="rt_will_be_rejected",
            privy_session_cookie="sess_cookie",
            state="needs_refresh",
        )
    )

    result = refresh_once(cache=cache, http=_StubHttp401())

    after = cache.read()
    assert isinstance(result, RefreshResult)
    assert result.state_after == "needs_otp"
    assert after.state == "needs_otp"


# ---------------------------------------------------------------------------
# Test 6: auth_facade falls through to TokenAcquirer when cache=needs_otp


def test_auth_facade_falls_through_to_otp_when_cache_needs_otp(tmp_path: Path) -> None:
    cache = SessionCache(path=tmp_path / "privy_session.json")
    cache.write(SessionCacheEntry(state="needs_otp"))

    acquirer_calls: list[dict] = []

    def stub_acquirer(**kwargs) -> AcquiredSession:
        acquirer_calls.append(kwargs)
        return AcquiredSession(
            jwt="eyJ.fresh.jwt",
            expires_at="2026-05-07T13:00:00+00:00",
            refresh_token_present=True,
            prophet_viewer_id="viewer_fixture_001",
        )

    def stub_refresher(**_kwargs) -> RefreshResult:  # should NOT be called
        raise AssertionError("refresher should not run when state=needs_otp")

    facade = AuthFacade(cache=cache, acquirer=stub_acquirer, refresher=stub_refresher)
    fresh = facade.get_fresh_jwt(
        email="implementer@example.com",
        provider="gmail",
        seren_user_id="user_fixture_001",
        bounty_id="bounty_fixture_001",
        browser_session=object(),  # stub, never used by stub_acquirer
        gateway=object(),
    )

    assert fresh.source == "otp"
    assert fresh.jwt == "eyJ.fresh.jwt"
    assert len(acquirer_calls) == 1
