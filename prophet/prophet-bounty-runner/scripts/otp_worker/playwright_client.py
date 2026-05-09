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


def capture_artifacts(session: BrowserSession, *, jwt: str) -> PrivyAuthArtifacts:
    """Snapshot the JWT plus the cookies needed for steady-state refresh."""
    return PrivyAuthArtifacts(
        jwt=jwt,
        refresh_token=session.get_cookie(PRIVY_REFRESH_COOKIE) or "",
        privy_token_cookie=session.get_cookie(PRIVY_TOKEN_COOKIE) or "",
        privy_session_cookie=session.get_cookie(PRIVY_SESSION_COOKIE) or "",
    )


class RealBrowserSession:
    """Concrete Playwright-backed BrowserSession for the Phase-14 live test.

    Wraps `sync_playwright` chromium. Use as a context manager so the
    browser process is reliably closed on exit:

        with RealBrowserSession(headless=True) as session:
            open_privy_modal(session)
            ...

    Plan §11.5 explicitly skipped a unit test for this class — it is
    the seam between the testable Protocol surface and Playwright's
    actual API. Live coverage comes from the Phase-14 acceptance run.
    """

    def __init__(self, *, headless: bool = True) -> None:
        # Imported lazily so non-live runs (and unit tests that stub
        # BrowserSession) do not pay the Playwright import cost.
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()

    def navigate(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded")

    def click(self, selector: str) -> None:
        self._page.click(selector)

    def fill(self, selector: str, value: str) -> None:
        self._page.fill(selector, value)

    def wait_for(self, selector: str, *, timeout_ms: int = 30_000) -> None:
        self._page.wait_for_selector(selector, timeout=timeout_ms)

    def get_local_storage(self, key: str) -> str | None:
        value = self._page.evaluate(
            "(k) => window.localStorage.getItem(k)", key
        )
        if isinstance(value, str):
            return value
        return None

    def dump_local_storage_keys(self) -> dict[str, str]:
        """Phase-14 diagnostic: enumerate every localStorage key + truncated value.

        Used only by `acquire_token`'s debug branch when
        `PROPHET_BOUNTY_DEBUG_LOCAL_STORAGE=1` is set in the environment.
        Truncates each value to 80 chars so JWTs don't end up in logs.
        """
        return self._page.evaluate(
            """() => {
                const out = {};
                for (let i = 0; i < window.localStorage.length; i++) {
                    const k = window.localStorage.key(i);
                    const v = window.localStorage.getItem(k);
                    out[k] = (v || '').slice(0, 80);
                }
                return out;
            }"""
        ) or {}

    def get_cookie(self, name: str) -> str | None:
        for cookie in self._context.cookies():
            if cookie.get("name") == name:
                value = cookie.get("value")
                if isinstance(value, str):
                    return value
        return None

    def close(self) -> None:
        try:
            self._context.close()
        except Exception:
            pass
        try:
            self._browser.close()
        except Exception:
            pass
        try:
            self._pw.stop()
        except Exception:
            pass

    def __enter__(self) -> "RealBrowserSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
