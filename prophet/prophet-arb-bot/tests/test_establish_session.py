"""Issue #638: `establish_browser_session_for_create` authenticates the
caller-supplied browser inside a single Playwright instance.

Three branches matter:

  (a) Cache is fresh AND Privy SDK boot observes the planted state.
      → restore is called once, acquirer is never called, the entry is
      returned as-is.

  (b) Cache is fresh BUT verification fails (Privy SDK didn't pick up
      the planted state — e.g. refresh token revoked server-side).
      → falls through to OTP cold-start on the SAME browser session,
      then returns whatever the cache holds afterwards.

  (c) Cache is stale / empty.
      → goes straight to OTP cold-start on the supplied browser.

The acquirer must always receive `browser_session=session` — that is
the entire point of the single-browser refactor.
"""

from __future__ import annotations

from typing import Any

from otp_worker.establish_session import (
    SessionEstablishmentFailed,
    establish_browser_session_for_create,
)
from otp_worker import (
    OtpEmailTimeout,
)


class _Entry:
    """Stand-in SessionCacheEntry."""

    def __init__(
        self,
        *,
        jwt: str = "",
        refresh_token: str = "",
        state: str = "needs_otp",
        fresh: bool = False,
    ) -> None:
        self.jwt = jwt
        self.refresh_token = refresh_token
        self.state = state
        self._fresh = fresh

    def is_fresh(self, *, leeway_seconds: int = 60) -> bool:
        return self._fresh


class _StubCache:
    def __init__(self, entries: list[_Entry]) -> None:
        # Pop one per `read()` so we can model "cache contents changed
        # after acquirer wrote a fresh row".
        self._entries = list(entries)

    def read(self) -> _Entry:
        if len(self._entries) == 1:
            return self._entries[0]
        return self._entries.pop(0)


class _Session:
    """Minimal BrowserSession stub.

    `_privy_session_observable` reads `get_local_storage('privy:user')`
    first and falls back to a `wait_for(SEL_CONNECT_BUTTON, ...)` probe
    — `observable` toggles which path returns True.
    """

    def __init__(self, *, observable: bool) -> None:
        self._observable = observable

    def get_local_storage(self, key: str) -> str | None:
        return "u_123" if (self._observable and key == "privy:user") else None

    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None:
        if self._observable:
            raise TimeoutError("SIGN IN never appeared")
        # Pretend the SIGN IN button appeared → caller treats as unauth.


def test_establish_returns_when_fresh_cache_and_privy_observable() -> None:
    cache = _StubCache([_Entry(jwt="j", refresh_token="r", state="fresh", fresh=True)])
    session = _Session(observable=True)

    restore_calls: list[dict[str, Any]] = []
    acquirer_calls: list[dict[str, Any]] = []

    def restore(s: Any, *, jwt: str, refresh_token: str) -> None:
        restore_calls.append({"jwt": jwt, "refresh_token": refresh_token})

    def acquirer(**kw: Any) -> None:
        acquirer_calls.append(kw)

    result = establish_browser_session_for_create(
        session=session,
        email="e@example.com",
        provider="gmail",
        seren_user_id="",
        bounty_id="",
        config_gateway=object(),
        transport=object(),
        pw_gateway=None,
        cache=cache,
        acquirer=acquirer,
        refresher=lambda **_: None,
        restore=restore,
    )

    assert restore_calls == [{"jwt": "j", "refresh_token": "r"}]
    assert acquirer_calls == []  # never reached the OTP fallback
    # The entry returned is the fresh one we put in the cache.
    assert getattr(result, "jwt", "") == "j"
    assert getattr(result, "refresh_token", "") == "r"


def test_establish_falls_through_to_acquirer_when_verification_fails() -> None:
    """Fresh cache, restore succeeds, but Privy SDK refused to pick it up.

    The function must then OTP-cold-start on the SAME session. The acquirer
    receives the supplied browser_session arg — that's the whole point of
    the single-browser refactor.
    """
    # First read: the "fresh" entry. Second read (after acquirer):
    # whatever the acquirer just wrote.
    cache = _StubCache(
        [
            _Entry(jwt="stale_j", refresh_token="stale_r", state="fresh", fresh=True),
            _Entry(jwt="new_j", refresh_token="new_r", state="fresh", fresh=True),
        ]
    )
    session = _Session(observable=False)

    acquirer_calls: list[dict[str, Any]] = []

    def restore(s: Any, *, jwt: str, refresh_token: str) -> None:
        # restore ran, but Privy didn't observe it — verify fails.
        return None

    def acquirer(**kw: Any) -> None:
        acquirer_calls.append(kw)

    result = establish_browser_session_for_create(
        session=session,
        email="e@example.com",
        provider="gmail",
        seren_user_id="",
        bounty_id="",
        config_gateway=object(),
        transport=object(),
        pw_gateway=None,
        cache=cache,
        acquirer=acquirer,
        refresher=lambda **_: None,
        restore=restore,
    )

    assert len(acquirer_calls) == 1
    # Acquirer ran on the SAME browser session.
    assert acquirer_calls[0]["browser_session"] is session
    # The returned entry is whatever the cache reports post-acquire.
    assert getattr(result, "jwt", "") == "new_j"


def test_establish_skips_restore_when_cache_is_stale() -> None:
    cache = _StubCache(
        [
            _Entry(jwt="", refresh_token="", state="needs_otp", fresh=False),
            _Entry(jwt="new_j", refresh_token="new_r", state="fresh", fresh=True),
        ]
    )
    session = _Session(observable=False)

    restore_calls: list[dict[str, Any]] = []
    acquirer_calls: list[dict[str, Any]] = []

    def restore(s: Any, *, jwt: str, refresh_token: str) -> None:
        restore_calls.append({"jwt": jwt, "refresh_token": refresh_token})

    def acquirer(**kw: Any) -> None:
        acquirer_calls.append(kw)

    result = establish_browser_session_for_create(
        session=session,
        email="e@example.com",
        provider="gmail",
        seren_user_id="",
        bounty_id="",
        config_gateway=object(),
        transport=object(),
        pw_gateway=None,
        cache=cache,
        acquirer=acquirer,
        refresher=lambda **_: None,
        restore=restore,
    )

    # No restore attempted — the cache had no usable material to restore.
    assert restore_calls == []
    assert len(acquirer_calls) == 1
    assert acquirer_calls[0]["browser_session"] is session
    assert getattr(result, "jwt", "") == "new_j"


def test_establish_raises_session_establishment_failed_on_otp_timeout() -> None:
    cache = _StubCache(
        [_Entry(jwt="", refresh_token="", state="needs_otp", fresh=False)]
    )
    session = _Session(observable=False)

    def acquirer(**_kw: Any) -> None:
        raise OtpEmailTimeout("no OTP email in 90s")

    try:
        establish_browser_session_for_create(
            session=session,
            email="e@example.com",
            provider="gmail",
            seren_user_id="",
            bounty_id="",
            config_gateway=object(),
            transport=object(),
            pw_gateway=None,
            cache=cache,
            acquirer=acquirer,
            refresher=lambda **_: None,
            restore=lambda *a, **kw: None,
        )
    except SessionEstablishmentFailed as exc:
        assert exc.reason.startswith("prophet_session_unavailable:OtpEmailTimeout")
    else:
        raise AssertionError("expected SessionEstablishmentFailed")
