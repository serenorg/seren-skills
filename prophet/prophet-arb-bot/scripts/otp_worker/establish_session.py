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


def _privy_session_observable(session: BrowserSession) -> bool:
    """Return True iff Privy reports an active session in this browser.

    Heuristic: after `restore_privy_session` navigated to the Prophet
    origin, an authenticated SDK boot leaves `privy:user` populated in
    localStorage. The SIGN IN button being absent is a secondary signal.
    Either is sufficient — neither is required individually.
    """
    try:
        user = session.get_local_storage("privy:user")
    except Exception:
        user = None
    if user:
        return True

    try:
        # Cheap probe: if SIGN IN button never appears within a short
        # budget, Privy considers us authenticated.
        session.wait_for(SEL_CONNECT_BUTTON, timeout_ms=1500)
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
