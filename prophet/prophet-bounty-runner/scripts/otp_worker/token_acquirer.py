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
    capture_artifacts,
    open_privy_modal,
    submit_email,
    submit_otp_code,
    wait_for_jwt,
)
from .session_cache import SessionCache, SessionCacheEntry

PRIVY_OTP_SENDER = "noreply@privy.io"
INBOX_POLL_INTERVAL_SECONDS = 3.0
INBOX_POLL_TIMEOUT_SECONDS = 90.0


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

    # Step 7: capture cookie set.
    artifacts = capture_artifacts(browser_session, jwt=jwt)
    if not artifacts.refresh_token:
        # Refresh-token cookie missing → steady-state refresher will not work.
        # Fail closed rather than ship a session that needs an OTP every cycle.
        raise PrivyAuthFailed("Privy did not set privy-refresh-token cookie")

    # Step 8: bind participant identity (P0 — plan §11.1 step 10, §3 ADR).
    viewer_id, viewer_email = _query_viewer(gateway=gateway, jwt=jwt)
    if viewer_email.casefold() != email.casefold():
        raise IdentityMismatch(
            f"Prophet viewer.email {viewer_email!r} does not match "
            f"inputs.prophet_email {email!r}"
        )

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


def _query_viewer(*, gateway: Any, jwt: str) -> tuple[str, str]:
    """Call prophet-ai's `viewer { id email }` and return (id, email).

    The real schema field names are confirmed during the Phase 6 schema
    probe; the agent-side Prophet client wraps this. This thin helper
    keeps the OTP worker self-contained and lets test_token_acquirer
    stub the entire viewer-binding step.
    """
    try:
        result = gateway.call(
            "prophet-ai",
            "POST",
            "/api/graphql",
            body={
                "query": "query Viewer { viewer { id email } }",
                "variables": {},
            },
            headers={"Authorization": f"Bearer {jwt}"},
        )
    except Exception as exc:
        raise PrivyAuthFailed(f"viewer query failed: {exc}") from exc

    viewer = ((result or {}).get("data") or {}).get("viewer") or {}
    viewer_id = viewer.get("id") or ""
    viewer_email = viewer.get("email") or ""
    if not viewer_id or not viewer_email:
        raise PrivyAuthFailed(
            f"viewer query returned incomplete payload: id={viewer_id!r} email={viewer_email!r}"
        )
    return viewer_id, viewer_email
