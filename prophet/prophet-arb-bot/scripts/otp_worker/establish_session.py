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
            if _privy_session_observable(session):
                return entry
            observable_diag = _capture_observable_check(session)

    # Silent restore failed / cache stale → drive OTP cold-start on the
    # same session so the authenticated browser is the one we hand back.
    try:
        acquirer(
            email=email,
            provider=provider,
            seren_user_id=seren_user_id,
            bounty_id=bounty_id,
            browser_session=session,
            gateway=pw_gateway if pw_gateway is not None else config_gateway,
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
