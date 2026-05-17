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
    """Caller should surface `prophet_session_unavailable`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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

    if entry.is_fresh() and entry.jwt and entry.refresh_token:
        try:
            restore(session, jwt=entry.jwt, refresh_token=entry.refresh_token)
        except Exception:
            entry = cache.read()  # keep the cached state; fall through
        else:
            if _privy_session_observable(session):
                return entry

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
        raise SessionEstablishmentFailed(
            f"prophet_session_unavailable:{type(exc).__name__}"
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

    Issue #658: `restore_privy_session` plants `privy:token` +
    `privy:refresh_token` at `document_start`. The Privy SDK then boots,
    validates the token, fetches the user, and finally writes
    `privy:user` to localStorage. That sequence routinely takes 1–4s in
    a cold MCP Chromium — far longer than the previous 1.5s SIGN IN
    probe. Polling `privy:user` over a budget (default 8s) catches the
    positive signal once the SDK lands it; the SIGN IN-absent probe is
    retained as a secondary signal at the end of the budget so truly
    revoked sessions still return False.
    """
    deadline = clock() + max(budget_seconds, 0.0)
    interval = max(poll_interval_seconds, 0.05)

    while True:
        try:
            user = session.get_local_storage("privy:user")
        except Exception:
            user = None
        if user:
            return True
        if clock() >= deadline:
            break
        sleep(interval)

    # Budget exhausted with `privy:user` still empty. Fall back to the
    # SIGN IN-absent probe: a slow SDK that ultimately failed will still
    # render the SIGN IN button; a slow SDK that ultimately succeeded
    # may have removed it without populating `privy:user` yet.
    try:
        session.wait_for(
            SEL_CONNECT_BUTTON, timeout_ms=_PRIVY_OBSERVABLE_SIGN_IN_PROBE_MS
        )
    except TimeoutError:
        return True
    except Exception:
        return False
    return False


__all__ = [
    "PROPHET_APP_URL",
    "SessionEstablishmentFailed",
    "establish_browser_session_for_create",
]
