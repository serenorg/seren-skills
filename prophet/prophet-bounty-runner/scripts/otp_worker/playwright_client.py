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
SEL_CONNECT_BUTTON = 'button:has-text("Connect")'
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


def wait_for_jwt(
    session: BrowserSession,
    *,
    poll_seconds: float = 1.0,
    timeout_seconds: float = 60.0,
) -> str:
    """Poll localStorage for privy:token until it appears or times out."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        token = session.get_local_storage(PRIVY_TOKEN_LOCAL_STORAGE_KEY)
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
