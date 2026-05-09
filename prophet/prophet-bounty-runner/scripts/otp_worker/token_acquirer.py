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

# Phase-14 live probe (2026-05-08): Privy now sends from
# `no-reply@mail.privy.io`, not `noreply@privy.io`. Documented as the
# second of two selector rotations seen during the live test (the
# Connect-button → SIGN IN rotation is the first; see playwright_client.py).
PRIVY_OTP_SENDER = "no-reply@mail.privy.io"
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
    viewer_id, viewer_email = _query_viewer(gateway=gateway, jwt=jwt, email=email)
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


def _query_viewer(*, gateway: Any, jwt: str, email: str = "") -> tuple[str, str]:
    """Call prophet-ai's `viewer { id email }` and return (id, email).

    Phase-14: if the first viewer call fails (most commonly because the
    user has a Privy identity but no Prophet user record bound to it),
    try `registerWithPrivy` once and re-query. Idempotent on the
    Prophet side: an already-registered user gets back their existing
    record, and a fresh user gets created. Either way the viewer query
    should succeed on retry.
    """
    try:
        return _viewer_call(gateway, jwt)
    except _ViewerNotAuthorized as exc:
        if not email:
            raise PrivyAuthFailed(f"viewer query failed: {exc}") from exc
        try:
            _register_with_privy(gateway=gateway, jwt=jwt, email=email)
        except Exception as register_exc:
            raise PrivyAuthFailed(
                f"viewer query failed: {exc}; registerWithPrivy fallback also failed: {register_exc}"
            ) from exc
        try:
            return _viewer_call(gateway, jwt)
        except Exception as retry_exc:
            raise PrivyAuthFailed(
                f"viewer query still failed after registerWithPrivy: {retry_exc}"
            ) from retry_exc


class _ViewerNotAuthorized(Exception):
    """Raised when Prophet rejects the viewer query (401 / no user record)."""


def _viewer_call(gateway: Any, jwt: str) -> tuple[str, str]:
    try:
        result = gateway.call(
            "prophet-ai",
            "POST",
            "/api/graphql",
            body={
                "query": (
                    "query Viewer { viewer { user { id email } "
                    "walletBalance { availableCents totalCents "
                    "onChainUsdc safeAddress safeDeployed } } }"
                ),
                "variables": {},
            },
            # Phase-14 live probe (2026-05-08): Prophet's `Viewer` type
            # has no top-level `id`/`email`; user identity lives at
            # `viewer.user`. Wallet balance lives at `viewer.walletBalance`
            # and gets folded into the participant_identity row so the
            # deposit-recommendation step has the data it needs.
            #
            # Auth: gateway needs SerenAPIKey on `Authorization` for
            # caller-auth/billing, so Privy JWT rides on the `Cookie`
            # header (`privy-token=<jwt>`) — the Prophet web app's
            # native auth channel and a documented passthrough header.
            headers={"Cookie": f"privy-token={jwt}"},
        )
    except Exception as exc:
        import os as _os, sys as _sys
        if _os.environ.get("PROPHET_BOUNTY_DEBUG_LOCAL_STORAGE") == "1":
            _sys.stderr.write(
                f"[diag] viewer_call_failed exc={exc!r} jwt_len={len(jwt)} "
                f"jwt_first16={jwt[:16]!r} jwt_dot_segments={jwt.count('.')}\n"
            )
        raise _ViewerNotAuthorized(str(exc)) from exc

    viewer = ((result or {}).get("data") or {}).get("viewer") or {}
    user = viewer.get("user") or {}
    viewer_id = user.get("id") or ""
    viewer_email = user.get("email") or ""
    if not viewer_id or not viewer_email:
        errors = (result or {}).get("errors") or []
        msg = errors[0].get("message", "viewer null") if errors else "viewer payload incomplete"
        raise _ViewerNotAuthorized(f"viewer payload empty: {msg}")
    return viewer_id, viewer_email


def _register_with_privy(*, gateway: Any, jwt: str, email: str) -> None:
    """Idempotent registerWithPrivy call. Errors propagate to caller.

    Phase-14: discovered via live introspection on 2026-05-08. Input
    fields are all SCALAR (email, fullName, eoaAddress, isWalletLogin),
    none NON_NULL, so passing only email is safe. Prophet-side schema
    is `Mutation: registerWithPrivy(input: RegisterWithPrivyInput!) ->
    PrivyRegistrationResult!`.
    """
    import os as _os, sys as _sys
    mutation = (
        "mutation RegisterWithPrivy($input: RegisterWithPrivyInput!) { "
        "registerWithPrivy(input: $input) { __typename } "
        "}"
    )
    result = gateway.call(
        "prophet-ai",
        "POST",
        "/api/graphql",
        body={
            "query": mutation,
            "variables": {"input": {"email": email, "isWalletLogin": False}},
        },
        headers={"Cookie": f"privy-token={jwt}"},
    )
    if _os.environ.get("PROPHET_BOUNTY_DEBUG_LOCAL_STORAGE") == "1":
        errors = (result or {}).get("errors") or []
        _sys.stderr.write(
            f"[diag] registerWithPrivy result.data={(result or {}).get('data')!r} "
            f"errors={errors!r}\n"
        )
