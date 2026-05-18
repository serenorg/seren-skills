"""Issue #687 — PRIVY_COMPATIBLE_ENV must not force headed Chrome.

#684 wired SEREN_PLAYWRIGHT_HEADLESS=0 into every gateway spawn site on
the recommendation in seren-desktop#1957's README, which warned that
*headless Chromium* has iframe regressions that break Privy's embedded
wallet sandbox. #685/#686 then routed the bundled MCP to **real Google
Chrome** via BROWSER_TYPE=chrome.

Mechanical evidence (2026-05-18 08:20 UTC, captured via ps -o command=):

    Connected MCP (Privy provisions in ~5s)  → Chrome launched HEADLESS
        --headless --hide-scrollbars --mute-audio --blink-settings=...

    Bundled MCP under PRIVY_COMPATIBLE_ENV   → Chrome launched HEADED
        SEREN_PLAYWRIGHT_HEADLESS=0 → shouldLaunchHeadless() returns false

Both launch the SAME Chrome binary at /Applications/Google Chrome.app.
Both apply identical stealth-plugin evasion config (PRIVY_COMPATIBLE_ENV
drops iframe.contentWindow + navigator.permissions). Both apply identical
automation flags (--disable-blink-features=AutomationControlled etc.).
The only meaningful difference is headed vs headless.

So the Desktop #1957 README warning was Chromium-specific: real Chrome
in headless mode does not have the iframe regression that vanilla
Chromium does. With BROWSER_TYPE=chrome in place, headless is in fact
the *better* path because that's what Privy is actually tested against.

The fix is mechanical: drop SEREN_PLAYWRIGHT_HEADLESS=0 from
PRIVY_COMPATIBLE_ENV. The bundled MCP's shouldLaunchHeadless()
(browser.ts:362) defaults to headless when the var is unset:

    return env[PLAYWRIGHT_HEADLESS_ENV] !== "0";

One critical test, no duplicates: pins the headless contract tied to
#687. The existing exact-equality test in test_playwright_mcp_privy_env.py
catches the broader profile shape; this file documents *why* headless
is the right default once BROWSER_TYPE=chrome is in play.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from otp_worker import playwright_mcp_gateway as pmg


def test_privy_compatible_env_does_not_force_headed_chrome() -> None:
    """PRIVY_COMPATIBLE_ENV MUST NOT set SEREN_PLAYWRIGHT_HEADLESS=0.

    The bundled playwright-stealth MCP's shouldLaunchHeadless()
    (browser.ts:362) returns false only when the var equals literal
    "0". Any other value — including omission, "1", "true", or empty
    string — yields headless mode, which is the path Privy is
    actually tested against on real Chrome (BROWSER_TYPE=chrome).

    Setting "0" launches headed Chrome and Privy's embedded-wallet
    provisioning times out at 30s; see issue #687 for the comparative
    argv diff against the connected MCP that provisions in ~5s.
    """
    headless = pmg.PRIVY_COMPATIBLE_ENV.get("SEREN_PLAYWRIGHT_HEADLESS")
    assert headless != "0", (
        "PRIVY_COMPATIBLE_ENV[SEREN_PLAYWRIGHT_HEADLESS] must not be '0' "
        "(headed mode). The bundled MCP under BROWSER_TYPE=chrome times "
        "out at 30s on Privy embedded-wallet provisioning in headed mode "
        "while the connected MCP provisions in ~5s in headless mode on "
        "the same Chrome binary. See issue #687 for the argv diff. "
        f"Got {headless!r}."
    )
