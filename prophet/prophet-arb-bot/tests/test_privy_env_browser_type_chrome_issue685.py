"""Issue #685 — PRIVY_COMPATIBLE_ENV must include BROWSER_TYPE=chrome.

#684 finished wiring the Privy-compatible env profile (HEADLESS=0, two
stealth evasions dropped, page-init-patch off) into every prophet-arb-bot
gateway spawn site, including OTP cold-start. Mechanically verified via
``ps eww`` on the cycle's MCP child — all three env vars reach the
bundled MCP correctly.

Yet ``--command run --yes-live`` still blocks at
``blocked_otp:OtpEmailTimeout:privy:connections did not appear in
localStorage within 30s``, while the connected MCP (same Prophet
account, same OTP path) provisions the embedded wallet in ~5 seconds.

Side-by-side ``ps`` evidence (2026-05-18 07:50 UTC) identifies the root
cause: the connected MCP launches Google Chrome
(``/Applications/Google Chrome.app/Contents/MacOS/Google Chrome``)
while the bundled MCP falls through to Playwright's bundled Chromium.

Privy's embedded-wallet provisioning relies on Chrome-specific surfaces
(Widevine CDM, GAIA identity, WebAuthn platform authenticator) that
vanilla Chromium doesn't ship. The bundled MCP already reads
``BROWSER_TYPE`` (browser.ts:309) and routes ``chrome`` either to the
installed-browser registry's ``executablePath`` or to Playwright's
``channel="chrome"`` lookup — same path the connected MCP uses.

The fix is a one-line addition to ``PRIVY_COMPATIBLE_ENV``. This test
pins the specific BROWSER_TYPE=chrome contract tied to #685 so a future
refactor can't silently drop it without explaining where Privy will
break.

One critical test, no duplicates: the existing equality check in
``test_playwright_mcp_privy_env.py`` enforces the broader profile shape;
this file documents *why* BROWSER_TYPE=chrome is part of that shape.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from otp_worker import playwright_mcp_gateway as pmg


def test_privy_compatible_env_pins_browser_type_chrome() -> None:
    """PRIVY_COMPATIBLE_ENV MUST set BROWSER_TYPE=chrome.

    Drives the bundled playwright-stealth MCP to launch Google Chrome via
    Playwright's installed-browser-registry path (same as the connected
    MCP) instead of falling through to vanilla Chromium. Privy's embedded
    wallet provisioning requires Chrome-specific surfaces that Chromium
    does not ship — drop this and a fresh-cache cycle reverts to
    ``OtpEmailTimeout: privy:connections did not appear``.
    """
    browser_type = pmg.PRIVY_COMPATIBLE_ENV.get("BROWSER_TYPE")
    assert browser_type == "chrome", (
        "PRIVY_COMPATIBLE_ENV[BROWSER_TYPE] must be 'chrome' so the bundled "
        "playwright-stealth MCP launches Google Chrome instead of bundled "
        "Chromium. Privy's embedded wallet needs Chrome-specific surfaces "
        "(Widevine CDM, GAIA identity, WebAuthn platform authenticator) that "
        "vanilla Chromium does not ship. See issue #685 for the comparative "
        f"evidence. Got {browser_type!r}."
    )
