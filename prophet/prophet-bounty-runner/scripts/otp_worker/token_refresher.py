"""Steady-state TokenRefresher: silent JWT rotation via Privy refresh-token.

Plan §11.2: every ~50 minutes (or 5 minutes before exp, whichever is
sooner) call Privy's session-refresh endpoint with the cached refresh-
token cookie, store the new JWT, bump expires_at. Fail closed on 401 by
flipping cache state to `needs_otp` — the next cron tick falls back to
the cold-start TokenAcquirer with no exception bubbled up.

The exact Privy refresh URL/payload must be re-confirmed via live
network capture during Phase 14 acceptance (plan §11.7). The state
machine, cache writes, and 401-handling logic in this file work
independent of the URL — tests stub the HTTP call entirely.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from . import PrivyAuthFailed
from .session_cache import SessionCache, SessionCacheEntry

# Privy session-refresh endpoint. The Privy SDK sends the refresh-token
# cookie as a request cookie; the server returns a new JWT in the
# response body and (sometimes) a rotated refresh-token via Set-Cookie.
# Confirm the exact path against a real network capture before Phase 14.
DEFAULT_PRIVY_REFRESH_URL = "https://auth.privy.io/api/v1/sessions"

REFRESH_INTERVAL_SECONDS = 50 * 60  # 50 minutes
REFRESH_LEEWAY_SECONDS = 5 * 60  # refresh once we're within 5 min of exp
MAX_CONSECUTIVE_FAILURES = 5  # back off and exit after this many


@dataclass
class RefreshResult:
    state_after: str  # 'fresh' | 'needs_refresh' | 'needs_otp'
    refreshed: bool
    detail: str = ""


class HttpRefresher(Protocol):
    """Seam for the HTTP call to Privy. Tests inject a stub."""

    def post_refresh(
        self, *, url: str, refresh_token: str, session_cookie: str
    ) -> tuple[int, dict[str, Any]]: ...


class _StdlibHttpRefresher:
    """Plain stdlib HTTP. No third-party deps."""

    def post_refresh(
        self, *, url: str, refresh_token: str, session_cookie: str
    ) -> tuple[int, dict[str, Any]]:
        cookies = []
        if refresh_token:
            cookies.append(f"privy-refresh-token={refresh_token}")
        if session_cookie:
            cookies.append(f"privy-session={session_cookie}")

        req = urllib.request.Request(
            url,
            data=b"{}",
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Cookie": "; ".join(cookies),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode() or "{}")
                return resp.status, payload
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode() or "{}")
            except Exception:
                payload = {}
            return exc.code, payload


def _decode_jwt_exp(jwt: str) -> str:
    import base64

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


def needs_refresh_now(
    entry: SessionCacheEntry, *, now: datetime, leeway_seconds: int = REFRESH_LEEWAY_SECONDS
) -> bool:
    """Return True if the cached JWT is missing, expired, or close to expiry."""
    if entry.state == "needs_otp":
        return False  # not refreshable; needs cold-start instead
    if entry.state == "needs_refresh":
        return True
    if not entry.jwt or not entry.jwt_expires_at:
        return True
    try:
        exp = datetime.fromisoformat(entry.jwt_expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return (exp - now).total_seconds() <= leeway_seconds


def refresh_once(
    *,
    cache: SessionCache,
    http: HttpRefresher | None = None,
    privy_url: str = DEFAULT_PRIVY_REFRESH_URL,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> RefreshResult:
    """Try one refresh cycle. Returns the resulting state.

    Behavior matrix per plan §11.2 + §11.6:
      - 200 + jwt        → cache.state = fresh, failures reset to 0.
      - 401 / session    → cache.state = needs_otp; next tick re-OTPs.
      - 5xx / network    → cache.state = needs_refresh; failure counter
                           increments. After MAX_CONSECUTIVE_FAILURES
                           we flip to needs_otp to avoid infinite retry.
    """
    http = http or _StdlibHttpRefresher()
    entry = cache.read()

    if entry.state == "needs_otp":
        # Nothing to refresh — caller falls through to TokenAcquirer.
        return RefreshResult(state_after="needs_otp", refreshed=False, detail="cache=needs_otp")
    if not entry.refresh_token:
        # No refresh token to send → cold-start required.
        entry.state = "needs_otp"
        cache.write(entry)
        return RefreshResult(
            state_after="needs_otp", refreshed=False, detail="no refresh_token in cache"
        )

    try:
        status, payload = http.post_refresh(
            url=privy_url,
            refresh_token=entry.refresh_token,
            session_cookie=entry.privy_session_cookie,
        )
    except Exception as exc:
        entry.state = "needs_refresh"
        entry.consecutive_refresh_failures += 1
        if entry.consecutive_refresh_failures >= MAX_CONSECUTIVE_FAILURES:
            entry.state = "needs_otp"
        cache.write(entry)
        return RefreshResult(
            state_after=entry.state,
            refreshed=False,
            detail=f"http error: {exc}",
        )

    if status == 401:
        # Refresh token expired or session revoked — must re-OTP.
        entry.state = "needs_otp"
        entry.consecutive_refresh_failures = 0
        cache.write(entry)
        return RefreshResult(state_after="needs_otp", refreshed=False, detail="401 from Privy")

    if status >= 500 or status >= 400:
        # Transient or unexpected — back off, stay refreshable.
        entry.state = "needs_refresh"
        entry.consecutive_refresh_failures += 1
        if entry.consecutive_refresh_failures >= MAX_CONSECUTIVE_FAILURES:
            entry.state = "needs_otp"
        cache.write(entry)
        return RefreshResult(
            state_after=entry.state,
            refreshed=False,
            detail=f"http status {status}",
        )

    # 2xx
    new_jwt = (
        payload.get("token")
        or payload.get("access_token")
        or payload.get("identity_token")
        or ""
    )
    if not new_jwt:
        # Privy returned 200 but no JWT — treat as transient.
        entry.state = "needs_refresh"
        entry.consecutive_refresh_failures += 1
        cache.write(entry)
        return RefreshResult(
            state_after=entry.state,
            refreshed=False,
            detail="200 but no token in payload",
        )

    rotated_refresh = (
        payload.get("refresh_token") or payload.get("privy_refresh_token") or ""
    )
    entry.jwt = new_jwt
    entry.jwt_expires_at = _decode_jwt_exp(new_jwt)
    if rotated_refresh:
        entry.refresh_token = rotated_refresh
    entry.last_refreshed_at = now().isoformat()
    entry.state = "fresh"
    entry.consecutive_refresh_failures = 0
    cache.write(entry)
    return RefreshResult(state_after="fresh", refreshed=True)


def run_refresh_loop(
    *,
    cache: SessionCache,
    http: HttpRefresher | None = None,
    privy_url: str = DEFAULT_PRIVY_REFRESH_URL,
    interval_seconds: float = REFRESH_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    should_continue: Callable[[], bool] = lambda: True,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> None:  # pragma: no cover - long-running loop, exercised by integration tests
    """Background loop. Runs alongside run_local_pull_runner.py.

    Stops itself if cache flips to needs_otp — the cron tick will then
    cold-start a fresh OTP cycle. No exception bubbles up.
    """
    while should_continue():
        entry = cache.read()
        if entry.state == "needs_otp":
            return
        if needs_refresh_now(entry, now=now()):
            refresh_once(cache=cache, http=http, privy_url=privy_url, now=now)
        sleep(interval_seconds)
