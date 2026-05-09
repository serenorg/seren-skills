"""Playwright-only logic for the Privy OTP browser dance.

Selectors come from the live Privy modal on app.prophetmarket.ai. They
are isolated here so token_acquirer.py stays unit-testable: the acquirer
takes a `browser_session` interface and tests inject a stub.

Live capture validation: re-confirm selectors against
https://app.prophetmarket.ai before each Phase-14 acceptance run.
Privy's modal has rotated id/name attributes in the past; if any of
these break, fix them in this file only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from . import OtpEmailTimeout, PrivyAuthFailed

PROPHET_APP_URL = "https://app.prophetmarket.ai"

# Selectors — keep these as named constants for easy diffing on UI changes.
# Re-validated against https://app.prophetmarket.ai 2026-05-08 (Phase 14).
# The login button text rotated from "Connect" to "SIGN IN" since the
# plan was written; fix selector rotations here only.
SEL_CONNECT_BUTTON = 'button:has-text("SIGN IN")'
SEL_EMAIL_INPUT = "#email-input"
SEL_EMAIL_SUBMIT = 'button:has-text("Submit")'
SEL_OTP_INPUT_TEMPLATE = 'input[name="code-{i}"]'  # 0..5

# Onboarding form selectors. After a successful Privy OTP the Prophet
# webapp redirects first-time users to `/onboarding` and gates User-row
# creation behind these two fields. The skill auto-fills both per
# operator direction (Prophet team approved on 2026-05-08).
SEL_ONBOARDING_USERNAME = "#username"
SEL_ONBOARDING_GEO_ATTESTATION = "#geo-attestation"
SEL_ONBOARDING_CONTINUE = 'button:has-text("Continue")'
ONBOARDING_URL_FRAGMENT = "/onboarding"

PRIVY_TOKEN_LOCAL_STORAGE_KEY = "privy:token"
PRIVY_REFRESH_COOKIE = "privy-refresh-token"
PRIVY_TOKEN_COOKIE = "privy-token"
PRIVY_SESSION_COOKIE = "privy-session"


@dataclass
class PrivyAuthArtifacts:
    jwt: str
    refresh_token: str
    privy_token_cookie: str
    privy_session_cookie: str


class BrowserSession(Protocol):
    """Minimal interface around Playwright we care about.

    The real implementation wraps Playwright's chromium/page; tests stub
    it without touching the network. Plan §11.5 explicitly skips
    test_playwright_client — this Protocol is the seam.
    """

    def navigate(self, url: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None: ...
    def get_local_storage(self, key: str) -> str | None: ...
    def get_cookie(self, name: str) -> str | None: ...
    def get_url(self) -> str: ...
    def is_checked(self, selector: str) -> bool: ...


def open_privy_modal(session: BrowserSession) -> None:
    """Navigate to Prophet, open the Privy auth modal."""
    session.navigate(PROPHET_APP_URL)
    session.wait_for(SEL_CONNECT_BUTTON, timeout_ms=30_000)
    session.click(SEL_CONNECT_BUTTON)
    session.wait_for(SEL_EMAIL_INPUT, timeout_ms=15_000)


def submit_email(session: BrowserSession, email: str) -> None:
    """Type the email and click Submit; Privy then sends the OTP."""
    session.fill(SEL_EMAIL_INPUT, email)
    session.click(SEL_EMAIL_SUBMIT)


def submit_otp_code(session: BrowserSession, code: str) -> None:
    """Fill the 6-digit OTP boxes; Privy submits on the last digit."""
    if len(code) != 6 or not code.isdigit():
        raise PrivyAuthFailed(f"refusing to submit non-6-digit code: {code!r}")
    for i, digit in enumerate(code):
        session.fill(SEL_OTP_INPUT_TEMPLATE.format(i=i), digit)


def _unwrap_jwt(raw: str | None) -> str | None:
    """Strip JSON-quote wrapping the Privy SDK adds when persisting tokens.

    Phase-14 live probe (2026-05-08): the Privy web SDK serializes
    `privy:token` via `JSON.stringify`, so `localStorage.getItem` returns
    `'"eyJ..."'` (literal surrounding double-quotes). Sending that as
    `Bearer "eyJ..."` makes Prophet's GraphQL upstream return 401
    Unauthorized. Real JWTs always start with `eyJ` (base64 `{"alg":...`),
    so stripping balanced surrounding quotes is safe.
    """
    if not raw:
        return raw
    s = raw.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        s = s[1:-1]
    return s or None


def wait_for_jwt(
    session: BrowserSession,
    *,
    poll_seconds: float = 1.0,
    timeout_seconds: float = 60.0,
) -> str:
    """Poll localStorage for privy:token until it appears or times out."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        token = _unwrap_jwt(session.get_local_storage(PRIVY_TOKEN_LOCAL_STORAGE_KEY))
        if token:
            return token
        time.sleep(poll_seconds)
    raise OtpEmailTimeout(
        f"privy:token did not appear in localStorage within {timeout_seconds:.0f}s"
    )


def at_onboarding_screen(session: BrowserSession) -> bool:
    """True iff the browser is sitting on Prophet's first-time onboarding form.

    First-time Privy logins land at `/onboarding?returnTo=/`; returning
    users skip straight back to `/`. Used by the token-acquirer flow to
    decide whether to drive the username + geo-attestation form.
    """
    try:
        return ONBOARDING_URL_FRAGMENT in session.get_url()
    except Exception:
        return False


def fill_onboarding_form(session: BrowserSession, *, username: str) -> None:
    """Auto-fill Prophet's first-time onboarding form and submit it.

    Per operator direction (Prophet team approved 2026-05-08):
    - username is generated from `prophet_email` upstream and passed in
    - the geo-attestation checkbox is auto-ticked

    On Prophet-side username uniqueness collision the caller is
    responsible for retrying with a hashed-suffix username; this
    function just drives the form once.
    """
    session.wait_for(SEL_ONBOARDING_USERNAME, timeout_ms=15_000)
    session.fill(SEL_ONBOARDING_USERNAME, username)
    if not session.is_checked(SEL_ONBOARDING_GEO_ATTESTATION):
        session.click(SEL_ONBOARDING_GEO_ATTESTATION)
    session.click(SEL_ONBOARDING_CONTINUE)


def capture_artifacts(session: BrowserSession, *, jwt: str) -> PrivyAuthArtifacts:
    """Snapshot the JWT plus the cookies needed for steady-state refresh."""
    return PrivyAuthArtifacts(
        jwt=jwt,
        refresh_token=session.get_cookie(PRIVY_REFRESH_COOKIE) or "",
        privy_token_cookie=session.get_cookie(PRIVY_TOKEN_COOKIE) or "",
        privy_session_cookie=session.get_cookie(PRIVY_SESSION_COOKIE) or "",
    )


_PLAYWRIGHT_PUBLISHER_SLUG = "playwright"


class RealBrowserSession:
    """Concrete BrowserSession that drives the Seren-managed Playwright MCP.

    Issue #470: this class used to bundle its own Python `playwright`
    runtime (`from playwright.sync_api import sync_playwright`), which
    broke every fresh install with `ModuleNotFoundError` and forked
    browser automation away from Seren's canonical Playwright publisher.

    Each `BrowserSession` method now maps to a single
    `gateway.call("playwright", "POST", "/<tool>", body=...)` so the
    Privy OTP dance routes through the same publisher seam as gmail,
    polymarket-data, prophet-ai, etc.

    Use as a context manager so the publisher-side session is reliably
    closed on exit:

        with RealBrowserSession(gateway=gateway, headless=True) as session:
            open_privy_modal(session)
            ...
    """

    def __init__(self, *, gateway: Any, headless: bool = True) -> None:
        self._gateway = gateway
        self._headless = headless

    # -- BrowserSession Protocol surface ------------------------------------

    def navigate(self, url: str) -> None:
        self._call("/navigate", {"url": url})

    def click(self, selector: str) -> None:
        self._call("/click", {"selector": selector})

    def fill(self, selector: str, value: str) -> None:
        self._call("/fill", {"selector": selector, "value": value})

    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None:
        self._call(
            "/wait_for_selector",
            {"selector": selector, "timeout": timeout_ms},
        )

    def get_local_storage(self, key: str) -> str | None:
        # `wait_for_jwt` runs `_unwrap_jwt` on the return value, so the
        # raw JSON-quoted form is preserved here intentionally.
        # `playwright_evaluate` only takes a `script` field, so the key
        # is templated inline (JSON-quoted to escape safely).
        script = (
            "(() => { const v = window.localStorage.getItem("
            + _js_string_literal(key)
            + "); return v === null ? null : v; })()"
        )
        result = self._call("/evaluate", {"script": script})
        unwrapped = _coerce_evaluate_result(result)
        return unwrapped if isinstance(unwrapped, str) else None

    def dump_local_storage_keys(self) -> dict[str, str]:
        """Diagnostic enumeration; gated on PROPHET_BOUNTY_DEBUG_LOCAL_STORAGE.

        Truncates each value to 80 chars so JWTs don't end up in logs.
        """
        script = (
            "(() => {"
            "  const out = {};"
            "  for (let i = 0; i < window.localStorage.length; i++) {"
            "    const k = window.localStorage.key(i);"
            "    const v = window.localStorage.getItem(k);"
            "    out[k] = (v || '').slice(0, 80);"
            "  }"
            "  return out;"
            "})()"
        )
        try:
            result = self._call("/evaluate", {"script": script})
        except Exception:
            return {}
        unwrapped = _coerce_evaluate_result(result)
        return unwrapped if isinstance(unwrapped, dict) else {}

    def get_cookie(self, name: str) -> str | None:
        # `document.cookie` cannot read HttpOnly cookies (privy-refresh-
        # token is HttpOnly), so the cookie read goes through a
        # publisher-side endpoint with access to BrowserContext.cookies().
        # The `PrivyAuthFailed("Privy did not set privy-refresh-token
        # cookie")` check in token_acquirer fails closed if missing.
        try:
            result = self._call("/get_cookie", {"name": name})
        except Exception:
            return None
        if isinstance(result, dict):
            value = result.get("value")
            if isinstance(value, str):
                return value
        if isinstance(result, str):
            return result
        return None

    def get_url(self) -> str:
        try:
            result = self._call("/evaluate", {"script": "window.location.href"})
        except Exception:
            return ""
        unwrapped = _coerce_evaluate_result(result)
        return unwrapped if isinstance(unwrapped, str) else ""

    def is_checked(self, selector: str) -> bool:
        try:
            result = self._call(
                "/evaluate",
                {
                    "script": (
                        "(() => { const el = document.querySelector("
                        + _js_string_literal(selector)
                        + "); return !!(el && el.checked); })()"
                    )
                },
            )
        except Exception:
            return False
        return bool(_coerce_evaluate_result(result))

    # -- Lifecycle ----------------------------------------------------------

    def close(self) -> None:
        try:
            self._call("/close", {})
        except Exception:
            pass

    def __enter__(self) -> "RealBrowserSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- Internal -----------------------------------------------------------

    def _call(self, path: str, body: dict[str, Any]) -> Any:
        return self._gateway.call(
            _PLAYWRIGHT_PUBLISHER_SLUG,
            "POST",
            path,
            body=body,
        )


def _js_string_literal(value: str) -> str:
    import json as _json

    return _json.dumps(value)


def _coerce_evaluate_result(result: Any) -> Any:
    """Unwrap the `playwright_evaluate` response envelope.

    Different Playwright MCP implementations return the evaluated value
    under different field names (`result`, `value`, `output`); fall back
    to the raw payload so we don't silently swallow something usable.
    """
    if isinstance(result, dict):
        for key in ("result", "value", "output"):
            if key in result:
                return result[key]
        return result
    return result
