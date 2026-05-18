"""Establish an authenticated Prophet browser session inside a single Playwright instance.

Issue #638: `cmd_create_market_via_ui` used to open two browser instances —
one for `_acquire_jwt` (OTP cold-start) and a second one for `/create`
driving — so the second browser had no Privy state and every entry into
`/create` redirected to `/?returnTo=/create`. The MCP-level
`wait_for(SEL_QUESTION_INPUT, ...)` then surfaced as a TimeoutError.

This module owns the "authenticate this caller-supplied session" step:

  1. If the cache is fresh, try silent HTTP refresh first (cheap, no
     OTP email). Then write the JWT + refresh token into the supplied
     browser via `restore_privy_session` (which uses
     `playwright_add_init_script` to plant Privy state at
     `document_start`) and verify the Privy SDK actually picked up the
     session.
  2. If verification fails (or no cached material), drive the existing
     `acquire_token` OTP cold-start on the SAME browser session. No
     second gateway. No second restore.

The caller hands us a `BrowserSession` and on success we return with
that browser sitting on `https://app.prophetmarket.ai` already
authenticated against Privy.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from . import (
    EmailPublisherUnavailable,
    IdentityMismatch,
    OtpEmailTimeout,
    PrivyAuthFailed,
)
from .playwright_client import (
    BrowserSession,
    PROPHET_APP_URL,
    SEL_CONNECT_BUTTON,
)
from .privy_restore import restore_privy_session
from .session_cache import SessionCache
from .token_acquirer import AcquiredSession, acquire_token
from .token_refresher import RefreshResult, refresh_once

# Issue #658: Privy SDK boot in a freshly-launched headless Chromium
# routinely takes 1–4s before it populates `privy:user` from the planted
# JWT. An 8s budget covers a slow cold launch under MCP/peer contention
# without making the negative path (truly revoked sessions) noticeably
# slower than the prior 1.5s probe.
_PRIVY_OBSERVABLE_BUDGET_SECONDS = 8.0
_PRIVY_OBSERVABLE_POLL_INTERVAL_SECONDS = 0.25
_PRIVY_OBSERVABLE_SIGN_IN_PROBE_MS = 1_500

# Issue #714: Probe whether the SDK rejected the planted session by
# navigating to a guarded route. ``restore_privy_session`` navigates to
# ``PROPHET_APP_URL`` (homepage), which doesn't require auth, so any
# observability check there is a false positive. ``/create`` is the
# guarded route the per-entry driver actually needs; if the SDK rejects
# the planted session it (a) calls ``destroyLocalState`` which clears
# ``privy:token`` and ``privy:refresh_token`` and (b) ``router.push``-es
# to ``/?returnTo=%2Fcreate``. Either signal is conclusive — poll for
# both over a budget large enough to cover SDK boot variance under MCP
# cold-launch contention (1-4s typical, 8s ceiling). If either fires,
# fall through to in-context OTP via ``acquirer()``.
#
# Budget matches ``_PRIVY_OBSERVABLE_BUDGET_SECONDS`` (8s) so the
# negative-signal window is at least as long as the positive-signal
# window the existing observable check uses. Pre-#714 the cache-fresh
# branch trusted only the positive signal and false-positived on the
# unauth-tolerant homepage; #714's probe is the negative-signal
# counterpart.
_PROBE_CREATE_URL = f"{PROPHET_APP_URL}/create"
_SDK_SETTLE_BUDGET_SECONDS = 8.0
_SDK_SETTLE_POLL_INTERVAL_SECONDS = 0.25


class SessionEstablishmentFailed(Exception):
    """Caller should surface `prophet_session_unavailable`.

    Optional `details` carries diagnostic context (e.g. `observable_check`)
    that the agent-level handler can attach to the blocked envelope so
    operators can tell which signal failed without re-running with a
    debugger. See issue #660.
    """

    def __init__(self, reason: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details: dict[str, Any] = dict(details) if details else {}


def establish_browser_session_for_create(
    *,
    session: BrowserSession,
    email: str,
    provider: str,
    seren_user_id: str,
    bounty_id: str,
    config_gateway: Any,
    transport: Any,
    pw_gateway: Any,
    cache: SessionCache | None = None,
    acquirer: Callable[..., AcquiredSession] = acquire_token,
    refresher: Callable[..., RefreshResult] = refresh_once,
    restore: Callable[..., None] = restore_privy_session,
) -> Any:
    """Authenticate `session` against Prophet inside the caller's browser.

    On success returns the `SessionCacheEntry` that is now authoritative
    for this browser — the caller threads `entry.jwt` into downstream
    GraphQL calls (e.g. `compute_seed_intent`). On failure raises
    `SessionEstablishmentFailed` with a snake_case reason.
    """
    cache = cache or SessionCache()
    entry = cache.read()

    if entry.state == "needs_refresh" and entry.refresh_token:
        refresher(cache=cache)
        entry = cache.read()

    # Issue #664: capture a non-PII snapshot of the entry the cache-fresh
    # guard is about to inspect. Surfaced in `SessionEstablishmentFailed.details`
    # on the OTP fall-through path so operators can tell why the guard
    # bypassed the restore branch even when the on-disk cache looks fresh.
    # No JWT bytes, no email — just the decision inputs the guard read.
    cache_check: dict[str, Any] = {
        "state": getattr(entry, "state", ""),
        "is_fresh": bool(entry.is_fresh()),
        "jwt_present": bool(getattr(entry, "jwt", "")),
        "refresh_token_present": bool(getattr(entry, "refresh_token", "")),
        "jwt_expires_at": getattr(entry, "jwt_expires_at", ""),
    }

    observable_diag: dict[str, Any] | None = None
    # Issue #666: Privy retired the localStorage refresh-token mechanism
    # server-side; the JWT alone is now the session. Drop the
    # ``entry.refresh_token`` term from this conjunction so JWT-only
    # entries enter the cache-fresh branch instead of being silently
    # bypassed (which would force an unnecessary OTP cold-start every
    # cycle). ``restore_privy_session`` plants only the JWT when the
    # refresh token is empty.
    if entry.is_fresh() and entry.jwt:
        try:
            restore(
                session,
                jwt=entry.jwt,
                refresh_token=entry.refresh_token,
                # Issue #676: plant the keys the SDK actually writes —
                # privy:connections carries the embedded wallet the
                # `/create` signing flow uses. Rollback of PR #675's
                # privy:pat / privy:id_token contract.
                privy_connections=getattr(entry, "privy_connections", "") or "",
                privy_caid=getattr(entry, "privy_caid", "") or "",
                privy_recent_login_method=(
                    getattr(entry, "privy_recent_login_method", "") or ""
                ),
                # Issue #705: restore the privy-session cookie too.
                # Prophet's middleware checks the HttpOnly cookie to
                # decide /create vs /?returnTo=/create; without this
                # the warm context is server-side unauthenticated even
                # when localStorage planting looks correct.
                privy_session_cookie=(
                    getattr(entry, "privy_session_cookie", "") or ""
                ),
                # Issue #707: also restore the JWT-bearing privy-token
                # cookie. #706 restored only privy-session and the
                # /create redirect persisted. Privy SDK writes both
                # cookies at login; Prophet's middleware checks both.
                privy_token_cookie=(
                    getattr(entry, "privy_token_cookie", "") or ""
                ),
            )
        except Exception as restore_exc:
            # Issue #662: the previous `except Exception: pass` silently
            # swallowed the failure and fell through to OTP cold-start,
            # which then masqueraded the real issue as
            # EmailPublisherUnavailable on operators with no email
            # publisher connected. Fail closed with a dedicated reason
            # and surface the underlying exception so the operator can
            # identify which MCP call failed.
            raise SessionEstablishmentFailed(
                "prophet_session_unavailable:restore_failed",
                details={
                    "restore_exception": {
                        "type": type(restore_exc).__name__,
                        "message": str(restore_exc)[:200],
                    }
                },
            ) from restore_exc
        else:
            # Issue #714: probe whether the SDK actually accepts the
            # planted session by navigating to ``/create`` (the guarded
            # route the per-entry driver needs). ``restore`` navigated
            # to the homepage, which doesn't require auth — observing
            # planted state there is a false positive that lets a
            # cross-context-replayed session sail past
            # ``_privy_session_observable`` and then fail per-entry
            # with ``ocs_session_id_not_captured``. See #713 close
            # comment for the proof that ``/create`` redirect is purely
            # client-side: the page itself is statically prerendered
            # (200 + 23KB body with or without cookies), and the
            # ``/?returnTo=%2Fcreate`` redirect comes from the Privy
            # SDK running ``destroyLocalState`` + ``router.push`` after
            # its server-side ``users/me`` validation rejects the
            # replayed session. Detecting that rejection here is what
            # lets the existing fall-through to ``acquirer(...)`` do
            # an in-context OTP and produce a working session.
            if _sdk_rejected_planted_session_at_create(session):
                observable_diag = _capture_observable_check(session)
            elif _privy_session_observable(session):
                return entry
            else:
                observable_diag = _capture_observable_check(session)

    # Silent restore failed / cache stale → drive OTP cold-start on the
    # same session so the authenticated browser is the one we hand back.
    #
    # Issue #714 follow-up: ``acquirer``'s ``gateway`` argument is used to
    # construct the Gmail/Outlook InboxReader (see
    # ``otp_worker/inbox_reader.py:make_inbox_reader``) which calls
    # ``gateway.call(publisher=...)`` — that is the HttpGateway publisher
    # path, NOT the Playwright MCP gateway. The pre-#714 wiring passed
    # ``pw_gateway`` when present, which would have raised
    # ``EmailPublisherUnavailable`` from inside the inbox reader the moment
    # the warm-context OTP fall-through fired. The pre-#714 observability
    # check on the homepage false-positived 100% of the time, so this code
    # path was never actually exercised in production — #714's probe-
    # /create check makes it reachable, exposing the latent bug. Always
    # pass ``config_gateway`` here; ``pw_gateway`` is the wrong type for
    # publisher routing. ``browser_session`` separately carries the
    # Playwright handle the acquirer uses for the in-browser OTP dance.
    try:
        acquirer(
            email=email,
            provider=provider,
            seren_user_id=seren_user_id,
            bounty_id=bounty_id,
            browser_session=session,
            gateway=config_gateway,
            transport=transport,
            cache=cache,
        )
    except (
        OtpEmailTimeout,
        EmailPublisherUnavailable,
        PrivyAuthFailed,
        IdentityMismatch,
    ) as exc:
        details: dict[str, Any] = {"cache_check": cache_check}
        if observable_diag is not None:
            details["observable_check"] = observable_diag
        raise SessionEstablishmentFailed(
            f"prophet_session_unavailable:{type(exc).__name__}",
            details=details,
        ) from exc

    # Re-read the cache: the acquirer wrote a fresh entry on success.
    return cache.read()


def _privy_session_observable(
    session: BrowserSession,
    *,
    budget_seconds: float = _PRIVY_OBSERVABLE_BUDGET_SECONDS,
    poll_interval_seconds: float = _PRIVY_OBSERVABLE_POLL_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Return True iff Privy reports an active session in this browser.

    Issue #660: the previous heuristic was stricter than the downstream
    `is_session_healthy` check that detects mid-batch auth loss. The
    SDK does not always write `privy:user` on token-restore boots, and
    its slow boot under MCP cold-launch contention can leave the SIGN
    IN button briefly visible while the planted token is still being
    validated. The previous gate caught that visibility window and
    returned False, forcing a needless OTP fallback even though the
    planted `privy:token` was a healthy session.

    Align with `is_session_healthy`: the surviving planted state
    (`privy:token` OR `privy:user`) on the Prophet origin is the
    positive signal. The SDK actively clears the planted token when it
    rejects the session, so the absence of both keys — together with a
    redirect off the Prophet origin or a persistent SIGN IN button — is
    the negative signal.
    """
    deadline = clock() + max(budget_seconds, 0.0)
    interval = max(poll_interval_seconds, 0.05)

    while True:
        if _read_planted_state(session) and _on_prophet_origin(session):
            return True
        if clock() >= deadline:
            break
        sleep(interval)

    # Budget exhausted without a surviving session signal. Fall back to
    # the SIGN IN-absent probe to give a SDK that suppressed planted
    # state but kept us on the Prophet origin one last chance.
    try:
        session.wait_for(
            SEL_CONNECT_BUTTON, timeout_ms=_PRIVY_OBSERVABLE_SIGN_IN_PROBE_MS
        )
    except TimeoutError:
        return True
    except Exception:
        return False
    return False


def _read_planted_state(session: BrowserSession) -> str | None:
    """Mirror `is_session_healthy`'s positive-signal scan over Privy keys."""
    for key in ("privy:token", "privy:user"):
        try:
            value = session.get_local_storage(key)
        except Exception:
            value = None
        if value:
            return value
    return None


def _on_prophet_origin(session: BrowserSession) -> bool:
    """Reject sessions that have already been redirected off Prophet."""
    get_url = getattr(session, "get_url", None)
    if get_url is None:
        # Older browser stubs (and the existing observability tests)
        # don't expose `get_url`; treat as "no URL evidence to reject".
        return True
    try:
        url = get_url() or ""
    except Exception:
        return True
    if "app.prophetmarket.ai" not in url:
        return False
    if "returnTo=" in url or "/login" in url:
        return False
    return True


def _sdk_rejected_planted_session_at_create(
    session: BrowserSession,
    *,
    budget_seconds: float = _SDK_SETTLE_BUDGET_SECONDS,
    poll_interval_seconds: float = _SDK_SETTLE_POLL_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Probe whether the Privy SDK rejects the planted session.

    Issue #714: navigate to ``/create`` (the guarded route the per-entry
    driver needs) and poll for either of the two signals the Privy SDK
    emits on session rejection:

    1. **URL redirect to ``/?returnTo=``.** The SDK's ``router.push``
       after ``destroyLocalState`` redirects to the homepage with the
       ``returnTo`` query param.

    2. **``privy:token`` cleared from localStorage.** The SDK's
       ``destroyLocalState`` wipes the token-bearing keys atomically.
       This signal sometimes fires before the redirect lands, giving
       us a slightly earlier negative signal.

    Either signal is conclusive. The first live test of #714 caught
    the URL signal in <3s on a fast cycle but missed it on a slower
    cycle where SDK boot took longer than the original 3s budget — at
    which point the observable check false-positived because the URL
    was still ``/create`` and ``privy:token`` was still present.
    Adding the localStorage signal plus expanding the budget to 8s
    (matching ``_PRIVY_OBSERVABLE_BUDGET_SECONDS``) closes that race.

    Stubs that don't expose ``navigate``, ``get_url``, or
    ``get_local_storage`` are tolerated — in that case the relevant
    signal is skipped. If both signal sources are unavailable, returns
    False (no evidence of rejection) so existing establish-path tests
    that mock minimal sessions don't need to add navigation plumbing.
    """
    navigate = getattr(session, "navigate", None)
    if callable(navigate):
        try:
            navigate(_PROBE_CREATE_URL)
        except Exception:
            # A navigation error doesn't itself prove rejection. Fall
            # through to the polls; if a prior page is still showing,
            # the polls will read its state and return False.
            pass

    get_url = getattr(session, "get_url", None)
    get_local_storage = getattr(session, "get_local_storage", None)
    if get_url is None and get_local_storage is None:
        # No way to read either rejection signal. Caller falls back to
        # the observable check.
        return False

    # Read the initial privy:token snapshot. If it was already empty
    # before the probe ran (no restore happened, or restore is broken),
    # we can't use "token cleared" as a signal — only an
    # initially-present-then-cleared transition is conclusive. Snapshot
    # outside the poll loop so the comparison is stable.
    initial_token_present: bool
    if callable(get_local_storage):
        try:
            initial_token = get_local_storage("privy:token")
            initial_token_present = bool(initial_token)
        except Exception:
            initial_token_present = False
    else:
        initial_token_present = False

    deadline = clock() + max(budget_seconds, 0.0)
    interval = max(poll_interval_seconds, 0.05)
    while True:
        # Signal A: URL redirected off the guarded route.
        if callable(get_url):
            try:
                url = get_url() or ""
            except Exception:
                url = ""
            if "returnTo=" in url:
                return True

        # Signal B: planted privy:token was destroyed mid-flight.
        # Only treat the transition (initially present → now absent) as
        # rejection. A token that was never planted to begin with is
        # not a signal — it's the absence of evidence.
        if initial_token_present and callable(get_local_storage):
            try:
                current_token = get_local_storage("privy:token")
            except Exception:
                current_token = None
            if not current_token:
                return True

        if clock() >= deadline:
            return False
        sleep(interval)


def _capture_observable_check(session: BrowserSession) -> dict[str, Any]:
    """Snapshot the post-restore state for the blocked envelope.

    Issue #660: when the establish path falls through to OTP, surface
    enough Privy / Prophet context that an operator can tell at a
    glance why the planted state was deemed unobservable. Stays cheap —
    one localStorage read per known key, one URL read.
    """
    state: dict[str, Any] = {
        "budget_seconds": _PRIVY_OBSERVABLE_BUDGET_SECONDS,
        "poll_interval_seconds": _PRIVY_OBSERVABLE_POLL_INTERVAL_SECONDS,
        "privy_token_present": False,
        "privy_user_present": False,
    }
    try:
        state["privy_token_present"] = bool(
            session.get_local_storage("privy:token")
        )
    except Exception:
        pass
    try:
        state["privy_user_present"] = bool(
            session.get_local_storage("privy:user")
        )
    except Exception:
        pass
    get_url = getattr(session, "get_url", None)
    if get_url is not None:
        try:
            state["url"] = get_url() or ""
        except Exception:
            state["url"] = ""
    return state


__all__ = [
    "PROPHET_APP_URL",
    "SessionEstablishmentFailed",
    "establish_browser_session_for_create",
]
