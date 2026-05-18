"""Issue #689 — PRIVY_COMPATIBLE_ENV must not disable stealth evasions or page-init patch.

Desktop #1957's README recommended disabling iframe.contentWindow +
navigator.permissions stealth evasions and turning off the bundled
MCP's hand-rolled page-init patch as the "Privy-compatible profile."
The empirical evidence is the opposite of that guidance.

A manual walk-through (2026-05-18 08:41 UTC) drove a truly cold OTP
login on app.prophetmarket.ai through the same bundled playwright-stealth
MCP code under Claude Code's connected-MCP attachment (env: {}, full
default stealth, page-init patch ON):

    privy:connections appeared in localStorage at elapsed_ms=251

That's a quarter of a second after OTP submit. The same machine under
agent.py with PRIVY_COMPATIBLE_ENV setting STEALTH_EVASIONS_DISABLE +
DISABLE_PAGE_INIT_PATCH times out at 30s in three consecutive cycles.

Mechanism: Privy's embedded-wallet provisioning probes
navigator.permissions.query and iframe.contentWindow surfaces and
expects the *stealth-plugin-modified* shape that real users see in
post-headless-quirk-patched browsers, not the raw headless-Chrome
shape. The stealth plugin's evasions emulate a real user's browser;
turning them off exposes Privy to the underlying headless quirks it
was tuned to avoid.

Fix: keep only BROWSER_TYPE=chrome (#685, required so we use real
Chrome's Widevine/GAIA/WebAuthn surfaces). Drop the evasion-disable
and page-init-patch-off settings. The bundled MCP's full default
stealth is what the connected MCP runs and what Privy is tested
against.

One critical test, no duplicates: pins the empty-evasion-disable +
page-init-patch-default contract tied to #689. The existing
exact-equality test in test_playwright_mcp_privy_env.py is updated in
lockstep; this file documents *why* full default stealth is the right
posture once the Chrome binary is correct.
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from otp_worker import playwright_mcp_gateway as pmg


def test_privy_compatible_env_keeps_full_default_stealth() -> None:
    """PRIVY_COMPATIBLE_ENV MUST NOT disable stealth evasions or the page-init patch.

    The bundled playwright-stealth MCP's shouldApplyStealthPlugin() and
    its hand-rolled addInitScript() default to ON unless overridden. The
    connected MCP runs them ON and Privy provisions the embedded wallet
    in ~250ms (see issue #689 walk-through evidence). Disabling either
    via the SEREN_PLAYWRIGHT_STEALTH_EVASIONS_DISABLE or
    SEREN_PLAYWRIGHT_DISABLE_PAGE_INIT_PATCH env vars makes Privy
    provisioning time out at the 30s OtpEmailTimeout guard.
    """
    evasions_disable = pmg.PRIVY_COMPATIBLE_ENV.get(
        "SEREN_PLAYWRIGHT_STEALTH_EVASIONS_DISABLE"
    )
    page_init_off = pmg.PRIVY_COMPATIBLE_ENV.get(
        "SEREN_PLAYWRIGHT_DISABLE_PAGE_INIT_PATCH"
    )
    assert evasions_disable is None, (
        "PRIVY_COMPATIBLE_ENV[SEREN_PLAYWRIGHT_STEALTH_EVASIONS_DISABLE] must be "
        "unset so the bundled MCP applies full default stealth evasions. Privy "
        "expects the stealth-plugin-modified surface for iframe.contentWindow "
        "and navigator.permissions; disabling those evasions exposes raw "
        "headless-Chrome quirks and Privy times out. See issue #689 walk-through "
        f"showing privy:connections in 251ms with full stealth. Got {evasions_disable!r}."
    )
    assert page_init_off is None, (
        "PRIVY_COMPATIBLE_ENV[SEREN_PLAYWRIGHT_DISABLE_PAGE_INIT_PATCH] must be "
        "unset so the bundled MCP applies its hand-rolled navigator.permissions "
        "page-init patch. Same evidence as evasions: connected MCP with page-init "
        "patch ON provisions Privy in ~250ms. See issue #689. "
        f"Got {page_init_off!r}."
    )
