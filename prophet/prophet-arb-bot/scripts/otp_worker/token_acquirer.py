"""Cold-start TokenAcquirer: orchestrates the Privy OTP dance.

Flow per plan §11.1:
  1. Open Privy modal in Playwright.
  2. Submit email.
  3. Poll inbox via gmail/outlook publisher every 3s, up to 90s.
  4. Extract the 6-digit code.
  5. Fill the OTP boxes.
  6. Poll localStorage["privy:token"] for the JWT.
  7. Capture refresh-token cookies.
  8. Bind viewer.id by calling prophet-ai's `viewer { id email }` query;
     fail-closed on email mismatch (IdentityMismatch).
  9. Persist the artifacts to SessionCache.

Each external dependency is injected so tests can stub without touching
the network. The acquirer never raises bare RuntimeError — every failure
maps to an OtpWorkerError subclass per plan §11.6.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from . import (
    EmailPublisherUnavailable,
    IdentityMismatch,
    OtpCodeNotFound,
    OtpEmailTimeout,
    PrivyAuthFailed,
)
from .inbox_reader import InboxReader, make_inbox_reader
from .otp_extractor import extract_otp_code
from .playwright_client import (
    BrowserSession,
    PrivyAuthArtifacts,
    at_onboarding_screen,
    capture_artifacts,
    fill_onboarding_form,
    open_privy_modal,
    submit_email,
    submit_otp_code,
    wait_for_jwt,
)
from .session_cache import SessionCache, SessionCacheEntry
from .username import base_username_from_email, collision_fallback

# Phase-14 live probe (2026-05-08): Privy now sends from
# `no-reply@mail.privy.io`, not `noreply@privy.io`. Documented as the
# second of two selector rotations seen during the live test (the
# Connect-button → SIGN IN rotation is the first; see playwright_client.py).
PRIVY_OTP_SENDER = "no-reply@mail.privy.io"
INBOX_POLL_INTERVAL_SECONDS = 3.0
INBOX_POLL_TIMEOUT_SECONDS = 90.0

# Prophet's `/onboarding` form redirects to `/` once the User row lands.
# The skill polls the URL off `/onboarding` to know when to proceed; if
# Prophet ever stalls the redirect (network, validation), bail out
# instead of looping forever.
ONBOARDING_POLL_INTERVAL_SECONDS = 1.0
ONBOARDING_POLL_TIMEOUT_SECONDS = 30.0


@dataclass
class AcquiredSession:
    jwt: str
    expires_at: str
    refresh_token_present: bool
    prophet_viewer_id: str


def _decode_jwt_exp(jwt: str) -> str:
    """Return the JWT's `exp` claim as ISO-8601 UTC, or empty on failure."""
    import base64
    import json

    try:
        _, payload_b64, _ = jwt.split(".")
        pad = "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_b64 + pad).decode())
        exp = int(claims.get("exp", 0))
        if not exp:
            return ""
        return datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
    except Exception:
        return ""


def acquire_token(
    *,
    email: str,
    provider: str,
    seren_user_id: str,
    bounty_id: str,
    browser_session: BrowserSession,
    gateway: Any,
    transport: Any = None,
    cache: SessionCache | None = None,
    inbox_reader: InboxReader | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> AcquiredSession:
    """Run the cold-start OTP dance end-to-end.

    Raises one of OtpEmailTimeout / OtpCodeNotFound / PrivyAuthFailed /
    EmailPublisherUnavailable / IdentityMismatch on failure. On success
    the SessionCache is updated atomically.
    """
    cache = cache or SessionCache()
    inbox = inbox_reader or make_inbox_reader(provider, gateway=gateway)

    started_at = now()

    # Steps 1-2: open modal, submit email.
    open_privy_modal(browser_session)
    submit_email(browser_session, email)

    # Steps 3-4: poll inbox for the OTP email.
    deadline = started_at.timestamp() + INBOX_POLL_TIMEOUT_SECONDS
    body: str | None = None
    while now().timestamp() < deadline:
        body = inbox.read_latest_otp_email(sender_filter=PRIVY_OTP_SENDER, since=started_at)
        if body:
            break
        sleep(INBOX_POLL_INTERVAL_SECONDS)
    if not body:
        raise OtpEmailTimeout(
            f"no Privy OTP email from {PRIVY_OTP_SENDER} within "
            f"{INBOX_POLL_TIMEOUT_SECONDS:.0f}s"
        )

    code = extract_otp_code(body)  # raises OtpCodeNotFound

    # Steps 5-6: submit the code, wait for JWT.
    submit_otp_code(browser_session, code)
    jwt = wait_for_jwt(browser_session)

    # Step 6b — first-time onboarding bind. Prophet redirects new users
    # to `/onboarding` and gates User-row creation behind a username +
    # geo-attestation form. Auto-fill both per operator direction
    # (Prophet team approved 2026-05-08); on Prophet-side username
    # uniqueness collision retry once with a hashed-suffix username.
    _drive_onboarding_if_present(
        session=browser_session, email=email, sleep=sleep, now=now
    )

    # Phase-14 diagnostic (gated on env var; disabled by default so
    # production cron runs do not leak token previews to stderr).
    import os as _os, sys as _sys
    if _os.environ.get("PROPHET_BOUNTY_DEBUG_LOCAL_STORAGE") == "1":
        try:
            dump = browser_session.dump_local_storage_keys()  # type: ignore[attr-defined]
        except Exception:
            dump = {"_dump_failed": "True"}
        _sys.stderr.write(
            f"[diag] localStorage keys: {sorted(dump.keys())}\n"
            f"[diag] jwt_len={len(jwt)} jwt_first16={jwt[:16]!r} jwt_last8={jwt[-8:]!r} "
            f"jwt_dot_segments={jwt.count('.')}\n"
        )

    # Step 7: capture cookie set.
    artifacts = capture_artifacts(browser_session, jwt=jwt)
    if not artifacts.refresh_token:
        # Refresh-token cookie missing → steady-state refresher will not work.
        # Fail closed rather than ship a session that needs an OTP every cycle.
        raise PrivyAuthFailed("Privy did not set privy-refresh-token cookie")

    # Step 8: bind participant identity (P0 — plan §11.1 step 10, §3 ADR).
    # Issue #493: direct-to-Prophet via ProphetDirectTransport.
    if transport is None:
        from prophet.transport import ProphetDirectTransport

        transport = ProphetDirectTransport()
    viewer_id, viewer_email = _query_viewer(transport=transport, jwt=jwt)
    if viewer_email.casefold() != email.casefold():
        raise IdentityMismatch(
            f"Prophet viewer.email {viewer_email!r} does not match "
            f"inputs.prophet_email {email!r}"
        )

    # Step 8b — bind the user to the AGENTACCESS affiliate code so that
    # markets they create are attributed on Prophet's affiliate-scoped
    # APIs (which the operator's reconciler queries). Idempotent: a
    # prior bind from an earlier cold-start surfaces an "already
    # redeemed" error that the helper swallows. See
    # prophet/affiliate.py for the wire shape and discovery notes.
    from prophet.affiliate import bind_agentaccess  # lazy import; avoids cycles

    try:
        bind_agentaccess(transport=transport, jwt=jwt)
    except Exception as exc:
        # Don't fail the cold-start: if the bind genuinely failed
        # (auth/schema drift) the operator's reconciler will report
        # zero markets and the issue will surface there, but the user
        # has a working session for everything else.
        import os as _os, sys as _sys
        if _os.environ.get("PROPHET_BOUNTY_DEBUG_LOCAL_STORAGE") == "1":
            _sys.stderr.write(f"[diag] bind_agentaccess failed: {exc!r}\n")

    # Step 9: persist atomically.
    expires_at = _decode_jwt_exp(jwt)
    entry = SessionCacheEntry(
        user_email=email,
        jwt=jwt,
        jwt_expires_at=expires_at,
        refresh_token=artifacts.refresh_token,
        privy_session_cookie=artifacts.privy_session_cookie,
        last_refreshed_at=now().isoformat(),
        state="fresh",
        consecutive_refresh_failures=0,
        prophet_viewer_id=viewer_id,
    )
    cache.write(entry)

    return AcquiredSession(
        jwt=jwt,
        expires_at=expires_at,
        refresh_token_present=True,
        prophet_viewer_id=viewer_id,
    )


_VIEWER_QUERY = (
    "query Viewer { viewer { user { id email } "
    "walletBalance { availableCents totalCents "
    "onChainUsdc safeAddress safeDeployed } } }"
)


def _query_viewer(*, transport: Any, jwt: str) -> tuple[str, str]:
    """Call Prophet's `viewer { user { id email } }` and return (id, email).

    Issue #493: routes directly to `app.prophetmarket.ai` via
    `ProphetDirectTransport` because the prophet-ai publisher hop is
    structurally incompatible with Prophet's Authorization-Bearer auth.
    Fails closed with PrivyAuthFailed on rejection — the agent re-runs
    the modal stack to recover.
    """
    try:
        result = transport.post_graphql(
            jwt=jwt, query=_VIEWER_QUERY, variables={}
        )
    except Exception as exc:
        import os as _os, sys as _sys
        if _os.environ.get("PROPHET_BOUNTY_DEBUG_LOCAL_STORAGE") == "1":
            _sys.stderr.write(
                f"[diag] viewer_call_failed exc={exc!r} jwt_len={len(jwt)} "
                f"jwt_first16={jwt[:16]!r} jwt_dot_segments={jwt.count('.')}\n"
            )
        raise PrivyAuthFailed(f"viewer query failed: {exc}") from exc

    viewer = ((result or {}).get("data") or {}).get("viewer") or {}
    user = viewer.get("user") or {}
    viewer_id = user.get("id") or ""
    viewer_email = user.get("email") or ""
    if not viewer_id or not viewer_email:
        errors = (result or {}).get("errors") or []
        msg = errors[0].get("message", "viewer null") if errors else "viewer payload incomplete"
        raise PrivyAuthFailed(f"viewer payload empty: {msg}")
    return viewer_id, viewer_email


def _drive_onboarding_if_present(
    *,
    session: BrowserSession,
    email: str,
    sleep: Callable[[float], None],
    now: Callable[[], datetime],
) -> None:
    """Drive Prophet's first-time onboarding form if the JWT landed there.

    Returning users skip onboarding entirely (`/onboarding` is never the
    landing URL for them), so this is a no-op for warm sessions. For
    cold-start users it fills the username + ticks the geo-attestation
    + clicks Continue, then polls until Prophet redirects off
    `/onboarding`. On Prophet-side username-uniqueness collision (the
    page stays on `/onboarding` past the timeout with the same username
    we submitted), retries once with the hashed-suffix fallback.
    """
    if not at_onboarding_screen(session):
        return

    candidates = [base_username_from_email(email), collision_fallback(email)]
    deadline_total = now().timestamp() + (
        ONBOARDING_POLL_TIMEOUT_SECONDS * len(candidates)
    )
    last_username = ""
    for username in candidates:
        last_username = username
        fill_onboarding_form(session, username=username)
        # Poll until the URL leaves `/onboarding`. If it does within the
        # per-attempt budget, we're done. Otherwise loop and try the
        # collision fallback.
        attempt_deadline = now().timestamp() + ONBOARDING_POLL_TIMEOUT_SECONDS
        while now().timestamp() < attempt_deadline:
            if not at_onboarding_screen(session):
                return
            sleep(ONBOARDING_POLL_INTERVAL_SECONDS)
        if now().timestamp() >= deadline_total:
            break

    raise PrivyAuthFailed(
        "Prophet onboarding did not complete within "
        f"{ONBOARDING_POLL_TIMEOUT_SECONDS * len(candidates):.0f}s "
        f"(last attempted username={last_username!r})"
    )
