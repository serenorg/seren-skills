"""Restore a cached Privy session into a Python-owned Playwright browser.

Issue #638: the previous implementation wrote `privy:token` /
`privy:refresh_token` to `window.localStorage` via `evaluate(...)` AFTER
navigating to the Prophet origin. By the time the writes landed the
Privy SDK had already booted and decided "no session", so the next
navigation to `/create` redirected to `/?returnTo=/create` and the
caller stalled on the SIGN IN modal.

With Seren Desktop's Playwright MCP now exposing
`playwright_add_init_script`, the canonical fix is to register the
restore script at `document_start` on every navigation. The Privy SDK
reads `localStorage` during its boot path, so by the time the SDK runs
the planted state is already there.

The Privy SDK serializes both tokens via `JSON.stringify` before
writing to localStorage, so each value must land wrapped in balanced
double-quotes for the SDK to unwrap correctly via
`localStorage.getItem(k).slice(1, -1)`.
"""

from __future__ import annotations

import json
from typing import Any

from .playwright_client import (
    PROPHET_APP_URL,
    PRIVY_REFRESH_LOCAL_STORAGE_KEY,
    PRIVY_TOKEN_LOCAL_STORAGE_KEY,
)

_PROPHET_ORIGIN = "https://app.prophetmarket.ai"


def restore_privy_session(session: Any, *, jwt: str, refresh_token: str) -> None:
    """Plant Privy session state into the caller's browser, then navigate.

    Registers a `document_start` init script that writes the JSON-quoted
    JWT + refresh token into `localStorage` on the Prophet origin, then
    navigates once. The init script persists for the lifetime of the
    browser context, so subsequent navigations (e.g. to `/create`) also
    see the planted state — no re-navigate hack needed.
    """
    if not jwt or not refresh_token:
        raise ValueError("restore_privy_session requires both jwt and refresh_token")

    script = _build_init_script(jwt=jwt, refresh_token=refresh_token)
    session.add_init_script(script)
    session.navigate(PROPHET_APP_URL)


def _build_init_script(*, jwt: str, refresh_token: str) -> str:
    """Build the JS that plants Privy state at `document_start`.

    The Privy SDK reads each value as a JSON string (it serializes with
    `JSON.stringify` when persisting), so we wrap each token in literal
    double-quotes before writing. Using `JSON.stringify` on both the key
    and the wrapped value as JS string literals keeps the injection
    safe — Privy's token format is JWT-shaped, but a malicious cache
    file would still be escaped by `json.dumps` before reaching JS.

    The origin guard is paranoia: the Python-owned browser only ever
    navigates to Prophet in this flow, but `add_init_script` fires for
    `about:blank` and any subframes too, and there is no reason to
    leak Privy tokens to those origins.
    """
    token_value = json.dumps(jwt)  # e.g. '"eyJ.j.w.t"'
    refresh_value = json.dumps(refresh_token)
    return (
        "(function () {"
        "  try {"
        "    if (window.location && window.location.origin === "
        + json.dumps(_PROPHET_ORIGIN)
        + ") {"
        "      window.localStorage.setItem("
        + json.dumps(PRIVY_TOKEN_LOCAL_STORAGE_KEY)
        + ", "
        + json.dumps(token_value)
        + ");"
        "      window.localStorage.setItem("
        + json.dumps(PRIVY_REFRESH_LOCAL_STORAGE_KEY)
        + ", "
        + json.dumps(refresh_value)
        + ");"
        "    }"
        "  } catch (e) {}"
        "})();"
    )
