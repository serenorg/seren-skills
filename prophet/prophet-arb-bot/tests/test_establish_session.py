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
    _privy_session_observable,
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

    def restore(s: Any, *, jwt: str, refresh_token: str, **_kw: Any) -> None:
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

    def restore(s: Any, *, jwt: str, refresh_token: str, **_kw: Any) -> None:
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

    def restore(s: Any, *, jwt: str, refresh_token: str, **_kw: Any) -> None:
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


def test_establish_fails_closed_with_restore_exception_when_restore_raises() -> None:
    """Issue #662: when `restore_privy_session` itself raises (MCP
    `add_init_script` rejected, `navigate` timed out, gateway stdio
    drop, etc.), the previous code silently swallowed the exception
    and fell through to OTP cold-start — burning an email round-trip
    and masquerading the real failure as `EmailPublisherUnavailable`.

    The fix: when restore raises AND the cache was otherwise fresh,
    raise `SessionEstablishmentFailed("prophet_session_unavailable:restore_failed")`
    directly and attach the raw exception type+message under
    `details['restore_exception']` so the operator can identify which
    underlying call failed.
    """
    cache = _StubCache(
        [_Entry(jwt="cached_j", refresh_token="cached_r", state="fresh", fresh=True)]
    )
    session = _Session(observable=False)  # never reached
    acquirer_calls: list[dict[str, Any]] = []

    class _BoomMcpError(RuntimeError):
        pass

    def restore(s: Any, *, jwt: str, refresh_token: str, **_kw: Any) -> None:
        raise _BoomMcpError("playwright_add_init_script rejected: tool not found")

    def acquirer(**kw: Any) -> None:
        acquirer_calls.append(kw)

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
            restore=restore,
        )
    except SessionEstablishmentFailed as exc:
        assert exc.reason == "prophet_session_unavailable:restore_failed", (
            f"want restore_failed reason, got {exc.reason!r}"
        )
        rx = exc.details.get("restore_exception")
        assert rx is not None, "restore_exception details must be attached"
        assert rx["type"] == "_BoomMcpError"
        assert "playwright_add_init_script rejected" in rx["message"]
        # Critical: OTP must NOT have been invoked — that's the masquerade
        # this fix is closing. The operator should not burn an OTP email
        # for a problem the underlying MCP raised before observability
        # even ran.
        assert acquirer_calls == [], (
            "OTP fallback must not fire when restore raises — got "
            f"{len(acquirer_calls)} acquirer calls"
        )
    else:
        raise AssertionError("expected SessionEstablishmentFailed")


def test_establish_attaches_observable_check_when_fresh_cache_falls_through() -> None:
    """Issue #660 acceptance #2: when a fresh cache restores but the
    Privy signal stays absent AND the OTP fallback also fails, the
    `SessionEstablishmentFailed.details["observable_check"]` block must
    carry the post-restore state so the agent's blocked envelope can
    surface it. Without this an operator sees `prophet_session_unavailable`
    with no payload context."""
    cache = _StubCache(
        [_Entry(jwt="cached_j", refresh_token="cached_r", state="fresh", fresh=True)]
    )

    class _UnobservableSession:
        # Restore succeeded but Privy SDK rejected the planted token,
        # cleared `privy:token`, and Prophet redirected us to /login.
        def get_local_storage(self, key: str) -> str | None:
            return None

        def get_url(self) -> str:
            return "https://app.prophetmarket.ai/?returnTo=/create"

        def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None:
            return  # SIGN IN visible → caller treats as unauth

    def acquirer(**_kw: Any) -> None:
        raise OtpEmailTimeout("no OTP email in 90s")

    try:
        establish_browser_session_for_create(
            session=_UnobservableSession(),
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
        check = exc.details.get("observable_check")
        assert check is not None, "observable_check must be attached on fall-through"
        assert check["privy_token_present"] is False
        assert check["privy_user_present"] is False
        assert "returnTo=" in check["url"]
        assert check["budget_seconds"] > 0
    else:
        raise AssertionError("expected SessionEstablishmentFailed")


def test_establish_enters_cache_fresh_branch_with_jwt_only_session() -> None:
    """Issue #666: Privy retired the localStorage refresh_token mechanism
    server-side and writes the literal sentinel ``"deprecated"`` into
    ``privy:refresh_token`` post-login. After #666's capture-time
    normalization, every freshly-bootstrapped cache will carry
    ``refresh_token=""`` and the JWT alone IS the session.

    The cache-fresh guard at ``establish_browser_session_for_create``
    must therefore accept JWT-only entries — dropping the
    ``entry.refresh_token`` term from the conjunction. Otherwise the
    guard rejects every post-#666 cache and the warm context bypasses
    the restore-then-observe sub-tree, forcing an OTP cold-start on
    every cycle even when the cached JWT is valid for 59 more minutes.

    The acceptance is:
    1. The cache-fresh branch IS entered (restore is called with the
       empty refresh_token, not skipped).
    2. The acquirer is NEVER called — no OTP email burned on a session
       that does not need refreshing.
    3. The function returns the cache entry as-is.
    """
    cache = _StubCache(
        [_Entry(jwt="cached_jwt", refresh_token="", state="fresh", fresh=True)]
    )
    session = _Session(observable=True)

    restore_calls: list[dict[str, Any]] = []
    acquirer_calls: list[dict[str, Any]] = []

    def restore(s: Any, *, jwt: str, refresh_token: str, **_kw: Any) -> None:
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

    assert restore_calls == [{"jwt": "cached_jwt", "refresh_token": ""}], (
        "JWT-only cache must enter the cache-fresh branch and call restore "
        f"with refresh_token=''; got {restore_calls!r}"
    )
    assert acquirer_calls == [], (
        "JWT-only cache must not fall through to OTP cold-start; "
        f"acquirer was called {len(acquirer_calls)} time(s)"
    )
    assert getattr(result, "jwt", "") == "cached_jwt"
    assert getattr(result, "refresh_token", "") == ""


def test_establish_falls_through_to_otp_when_sdk_redirects_create_to_returnto() -> None:
    """Issue #714: cross-context replay of a captured Privy session is
    rejected by the SDK at boot. ``restore_privy_session`` plants the
    cookies + localStorage and navigates to the homepage (which does
    not require auth). The pre-#714 observability check ran against
    the homepage and false-positived — planted state appeared present
    because the homepage's SDK boot hadn't decided yet. Then the
    per-entry driver navigated to ``/create``, the SDK ran validation,
    Privy rejected (server-side device binding), the SDK ran
    ``destroyLocalState`` + ``router.push('/?returnTo=/create')``, and
    the AI-seed-calc capture never fired.

    The #714 fix: probe ``/create`` directly inside
    ``establish_browser_session_for_create``. If the SDK redirects to
    ``/?returnTo=`` within the boot window, the planted session is
    proven unusable — fall through to in-context OTP via
    ``acquirer(...)`` instead of trusting it.

    Pin the contract: a session whose URL goes to ``/?returnTo=/create``
    after the probe navigation MUST trigger the OTP fall-through, and
    the acquirer MUST receive the same browser_session (so the OTP
    happens in the warm context that will then drive ``/create``).
    """
    cache = _StubCache(
        [
            _Entry(jwt="cached_j", refresh_token="", state="fresh", fresh=True),
            _Entry(jwt="post_otp_j", refresh_token="", state="fresh", fresh=True),
        ]
    )

    class _ReplayedSessionRejectedByPrivy:
        """Session that simulates the cross-context rejection.

        ``restore`` plants state and navigates to the homepage; the SDK
        boots, calls ``auth.privy.io/api/v1/users/me``, server-side
        device binding rejects, SDK clears the planted token and pushes
        ``/?returnTo=/create``. By the time the establish helper probes
        ``/create`` directly, the SDK has already executed its
        rejection path and the URL is on the homepage with returnTo.
        """

        def __init__(self) -> None:
            self._url = "https://app.prophetmarket.ai/"
            self.navigated_to: list[str] = []

        def navigate(self, url: str) -> None:
            self.navigated_to.append(url)
            # Privy SDK boots on /create, rejects the replayed session,
            # redirects to homepage with returnTo.
            self._url = "https://app.prophetmarket.ai/?returnTo=%2Fcreate"

        def get_url(self) -> str:
            return self._url

        # Stubs for _privy_session_observable's fall-back path — never
        # reached in the happy negative path, but present in case the
        # SDK-rejection probe ever short-circuits to it.
        def get_local_storage(self, key: str) -> str | None:
            return None

        def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None:
            return None

    session = _ReplayedSessionRejectedByPrivy()
    restore_calls: list[dict[str, Any]] = []
    acquirer_calls: list[dict[str, Any]] = []

    def restore(s: Any, *, jwt: str, refresh_token: str, **_kw: Any) -> None:
        # Real restore plants state and navigates to homepage. Mirror
        # that here so the probe's navigate-to-/create is the only
        # thing that triggers the rejection redirect.
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

    # The probe navigated to /create (the whole point of #714 — the
    # observability check on the homepage was a false positive).
    assert any(
        "/create" in u for u in session.navigated_to
    ), f"probe must navigate to /create; got {session.navigated_to!r}"

    # Cache replay was attempted, then proved unusable, then OTP ran
    # on the same session.
    assert len(restore_calls) == 1
    assert len(acquirer_calls) == 1
    assert acquirer_calls[0]["browser_session"] is session, (
        "OTP fall-through must hand off the warm session — that is the "
        "entire point of in-context OTP. #714 phase 1 keeps the two-"
        "BrowserContext architecture but routes OTP through the warm "
        "context whenever cross-context cache replay is detected as "
        "rejected."
    )
    # Returned entry comes from the cache after the OTP wrote fresh state.
    assert getattr(result, "jwt", "") == "post_otp_j"


def test_establish_attaches_cache_check_when_guard_bypassed_and_otp_fails() -> None:
    """Issue #664: when the cache-fresh guard at the top of
    `establish_browser_session_for_create` evaluates False, the function
    skips the restore + observability sub-tree entirely and falls
    straight through to OTP. If the OTP path then raises, the blocked
    envelope carries neither `restore_exception` (#662) nor
    `observable_check` (#660) — both of those are populated only inside
    the cache-fresh branch — so the operator sees a bare
    `prophet_session_unavailable:EmailPublisherUnavailable` with no clue
    why the guard rejected the cache.

    Fix: always capture a `cache_check` snapshot of the entry the guard
    actually saw (state, is_fresh result, jwt-present bool,
    refresh_token-present bool, jwt_expires_at) and attach it to
    `details["cache_check"]` whenever the OTP fall-through path raises.
    The snapshot carries no PII — no JWT bytes, no email — just the
    decision inputs the guard read.
    """
    cache = _StubCache(
        # Cache returns a state that the guard rejects: `state="needs_otp"`,
        # `is_fresh=False`, no jwt, no refresh_token. Exactly the
        # scenario the user saw in #664 according to the local replay:
        # one of these three is silently False at warm-context time even
        # though the on-disk JSON looked fresh.
        [_Entry(jwt="", refresh_token="", state="needs_otp", fresh=False)]
    )
    session = _Session(observable=False)

    from otp_worker import EmailPublisherUnavailable

    def acquirer(**_kw: Any) -> None:
        raise EmailPublisherUnavailable("no email publisher connected")

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
        # The reason must carry the OTP exception suffix so the existing
        # blocked-envelope wiring keeps working.
        assert exc.reason == "prophet_session_unavailable:EmailPublisherUnavailable"
        # Both legacy diagnostics stay absent in the bypass path — they
        # only fire inside the cache-fresh branch.
        assert exc.details.get("restore_exception") is None
        assert exc.details.get("observable_check") is None
        # NEW: cache_check must surface what the guard actually saw.
        cache_check = exc.details.get("cache_check")
        assert cache_check is not None, (
            "cache_check must be attached when the guard bypass falls "
            "through to OTP — that's the whole point of #664"
        )
        assert cache_check["state"] == "needs_otp"
        assert cache_check["is_fresh"] is False
        assert cache_check["jwt_present"] is False
        assert cache_check["refresh_token_present"] is False
        # Empty jwt_expires_at is the stale-cache marker — surface it
        # as-is so the operator can compare against the on-disk file.
        assert "jwt_expires_at" in cache_check
    else:
        raise AssertionError("expected SessionEstablishmentFailed")


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


# --- Issue #658: positive-signal poll for `privy:user` ----------------------
#
# The previous heuristic returned False whenever `privy:user` was missing on
# the first read AND the SIGN IN button appeared within 1500ms — both of
# which are normal during Privy SDK boot, so a fresh JWT was treated as
# unusable and the cycle bailed to OTP cold-start. The fix polls
# `privy:user` over a longer budget; SIGN IN-absent remains a fallback.


class _PollingSession:
    """Session stub that lets `privy:user` "land" after N polls.

    `user_reads` enumerates the values `get_local_storage('privy:user')`
    returns on successive calls. `sign_in_visible` toggles whether
    `wait_for(SEL_CONNECT_BUTTON, ...)` finds the button (visible == no
    TimeoutError == unauthenticated).
    """

    def __init__(
        self,
        *,
        user_reads: list[str | None],
        sign_in_visible: bool = True,
    ) -> None:
        self._user_reads = list(user_reads)
        self._sign_in_visible = sign_in_visible
        self.user_read_count = 0

    def get_local_storage(self, key: str) -> str | None:
        if key != "privy:user":
            return None
        self.user_read_count += 1
        if not self._user_reads:
            return None
        return self._user_reads.pop(0)

    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None:
        if self._sign_in_visible:
            return  # button visible → caller treats as unauthenticated
        raise TimeoutError("SIGN IN never appeared")


def test_privy_observable_polls_until_user_lands() -> None:
    """Privy SDK populates `privy:user` after a few hundred ms — the
    observability check must poll for it within budget instead of bailing
    on the first miss. Repro of issue #658: a fresh JWT was being treated
    as unusable and forcing the OTP cold-start path."""
    session = _PollingSession(
        # Two misses (planted token still booting), then `privy:user` lands.
        user_reads=[None, None, "u_123"],
        sign_in_visible=True,  # button stays visible during boot
    )
    sleeps: list[float] = []
    result = _privy_session_observable(
        session,
        budget_seconds=8.0,
        poll_interval_seconds=0.25,
        sleep=sleeps.append,
        clock=lambda: 0.0,
    )
    assert result is True
    assert session.user_read_count >= 3, (
        "observable must poll past first miss"
    )


def test_privy_observable_returns_false_when_user_never_lands() -> None:
    """If `privy:user` never lands AND SIGN IN button stays visible
    throughout the budget, observability returns False so the caller can
    fall through to OTP cold-start. This preserves the negative path for
    truly revoked sessions."""
    session = _PollingSession(
        user_reads=[None] * 20,  # never lands
        sign_in_visible=True,  # button visible whole time
    )
    # Mock clock so the polling loop exits deterministically.
    ticks = iter([0.0, 0.5, 2.0, 5.0, 9.0])
    result = _privy_session_observable(
        session,
        budget_seconds=8.0,
        poll_interval_seconds=0.25,
        sleep=lambda _s: None,
        clock=lambda: next(ticks),
    )
    assert result is False


# --- Issue #660: trust the planted Privy state when it survives ----------
#
# `is_session_healthy` (`scripts/otp_worker/playwright_mcp_gateway.py:226`)
# treats `privy:token` OR `privy:user` plus a Prophet-origin URL as a
# live session. The establish-time observability check was stricter than
# the downstream health check — it polled only `privy:user`, which Privy
# SDK does not always populate on token-restore boots, causing a fresh
# cached JWT to be false-negatived. The repro that drove #660 was: a
# brand-new bootstrap-OTP cache, planted via `add_init_script` into a
# second warm browser, was still rejected by the gate. Align the gate
# with the downstream criterion.


class _AlignedSession:
    """Stub that mirrors what a real browser exposes after `restore_privy_session`.

    Tests pin which of `privy:token` / `privy:user` the SDK preserves
    across boot and what the URL looks like.
    """

    def __init__(
        self,
        *,
        token: str | None,
        user: str | None,
        url: str,
        sign_in_visible: bool = False,
    ) -> None:
        self._state = {"privy:token": token, "privy:user": user}
        self._url = url
        self._sign_in_visible = sign_in_visible

    def get_local_storage(self, key: str) -> str | None:
        return self._state.get(key)

    def get_url(self) -> str:
        return self._url

    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None:
        if self._sign_in_visible:
            return
        raise TimeoutError("SIGN IN never appeared")


def test_privy_observable_trusts_planted_token_on_prophet_origin() -> None:
    """The Privy SDK does not always write `privy:user` on token-restore
    boots; what it DOES do is leave the planted `privy:token` in place
    on a valid session and clear it on a rejected one. After
    `restore_privy_session` planted the token and navigated to Prophet,
    `privy:token` being present alongside a Prophet-origin URL is a
    sufficient positive signal — same heuristic `is_session_healthy`
    uses mid-batch."""
    session = _AlignedSession(
        token="planted-jwt",  # planted, preserved across SDK boot
        user=None,  # SDK never wrote it
        url="https://app.prophetmarket.ai/",
    )
    # The fix should return True early on the first poll seeing the
    # planted token, so the budget never actually elapses. Pin the clock
    # to a single value — a still-looping implementation would be caught
    # by the next test's RED path, not this one.
    ticks = iter([0.0, 0.5, 1.0, 3.0])
    result = _privy_session_observable(
        session,
        budget_seconds=2.0,
        poll_interval_seconds=0.25,
        sleep=lambda _s: None,
        clock=lambda: next(ticks),
    )
    assert result is True


def test_privy_observable_trusts_planted_token_when_sign_in_briefly_visible() -> None:
    """The repro that drove #660: under MCP cold-launch contention, the
    Privy SDK can leave the SIGN IN button rendered while it is still
    validating the planted token. The previous heuristic caught that
    visibility window and returned False, forcing OTP fallback even
    though the planted `privy:token` was the SDK's accepted session.
    Fix: the surviving planted token IS the positive signal — same as
    `is_session_healthy` mid-batch — so don't fall back."""
    session = _AlignedSession(
        token="planted-jwt",  # SDK preserved it
        user=None,
        url="https://app.prophetmarket.ai/",
        sign_in_visible=True,  # button still painted while SDK hydrates
    )
    ticks = iter([0.0, 0.5, 1.0, 3.0])
    result = _privy_session_observable(
        session,
        budget_seconds=2.0,
        poll_interval_seconds=0.25,
        sleep=lambda _s: None,
        clock=lambda: next(ticks),
    )
    assert result is True


def test_privy_observable_rejects_when_token_cleared_and_redirected() -> None:
    """Negative path: the SDK cleared `privy:token` (session rejected)
    and Prophet redirected to `/?returnTo=`. Even if SIGN IN doesn't
    appear in 1.5s, the cleared planted state + redirect URL is a clear
    rejection."""
    session = _AlignedSession(
        token=None,  # SDK cleared the planted token
        user=None,
        url="https://app.prophetmarket.ai/?returnTo=/create",
        sign_in_visible=True,
    )
    ticks = iter([0.0, 0.5, 1.0, 2.5])
    result = _privy_session_observable(
        session,
        budget_seconds=2.0,
        poll_interval_seconds=0.25,
        sleep=lambda _s: None,
        clock=lambda: next(ticks),
    )
    assert result is False
