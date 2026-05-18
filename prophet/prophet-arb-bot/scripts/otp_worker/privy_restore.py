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

The Privy SDK serializes each token via `JSON.stringify` before writing
to localStorage, so each value must land wrapped in balanced double-
quotes for the SDK to unwrap correctly via
`localStorage.getItem(k).slice(1, -1)`.
"""

from __future__ import annotations

import json
from typing import Any

from .playwright_client import (
    PROPHET_APP_URL,
    PRIVY_ID_TOKEN_LOCAL_STORAGE_KEY,
    PRIVY_PAT_LOCAL_STORAGE_KEY,
    PRIVY_REFRESH_LOCAL_STORAGE_KEY,
    PRIVY_TOKEN_LOCAL_STORAGE_KEY,
)

_PROPHET_ORIGIN = "https://app.prophetmarket.ai"


def restore_privy_session(
    session: Any,
    *,
    jwt: str,
    refresh_token: str,
    privy_pat: str = "",
    privy_id_token: str = "",
) -> None:
    """Plant Privy session state into the caller's browser, then navigate.

    Registers a `document_start` init script that writes the JSON-quoted
    Privy state into ``localStorage`` on the Prophet origin, then
    navigates once. The init script persists for the lifetime of the
    browser context, so subsequent navigations (e.g. to ``/create``)
    also see the planted state.

    Issue #674: a manual diagnostic probe with a ``removeItem`` hook
    proved that ``Dy._getToken`` calls ``Dh.destroyLocalState`` within
    ~550ms of page boot when only a subset of the SDK's expected
    ``privy:*`` localStorage keys are present. Specifically the SDK
    wipes ``privy:token``, ``privy:refresh_token``, ``privy:pat``, and
    ``privy:id_token`` together. Planting ``privy:token`` alone (the
    pre-#674 contract) reliably triggers the wipe, which is why every
    ``/create`` cycle bounced to ``/?returnTo=/create`` and surfaced as
    ``ocs_session_id_not_captured``.

    The fix: plant ``privy:pat`` and ``privy:id_token`` alongside
    ``privy:token`` when the cache carries them.

    Issue #666: ``privy:refresh_token`` was retired server-side. An
    empty ``refresh_token`` (the post-#666 cache shape) is fine; we
    just skip that setter rather than planting an empty string that
    the SDK would treat as a corruption marker.
    """
    if not jwt:
        raise ValueError("restore_privy_session requires jwt")

    script = _build_init_script(
        jwt=jwt,
        refresh_token=refresh_token,
        privy_pat=privy_pat,
        privy_id_token=privy_id_token,
    )
    session.add_init_script(script)
    session.navigate(PROPHET_APP_URL)


def _setter_js(key: str, value: str) -> str:
    """Inline JS that writes ``localStorage[key] = JSON.stringify(value)``.

    The Privy SDK persists strings double-quoted (it serializes with
    ``JSON.stringify``), so we wrap once via ``json.dumps(value)``
    (producing ``"…"``-quoted JSON) and then ``json.dumps`` again to
    safely escape that into a JS string literal. The double-encoding
    keeps the injection safe — a malicious cache value can't escape its
    own quotes.
    """
    wrapped = json.dumps(value)
    return (
        "      window.localStorage.setItem("
        + json.dumps(key)
        + ", "
        + json.dumps(wrapped)
        + ");"
    )


def _build_init_script(
    *,
    jwt: str,
    refresh_token: str,
    privy_pat: str = "",
    privy_id_token: str = "",
) -> str:
    """Build the JS that plants Privy state at ``document_start``.

    The origin guard is paranoia: the Python-owned browser only ever
    navigates to Prophet in this flow, but ``add_init_script`` fires
    for ``about:blank`` and any subframes too, and there is no reason
    to leak Privy tokens to those origins.

    Each Privy key is planted only when we have a non-empty value for
    it. Per #674's diagnostic probe, planting an empty value (or the
    legacy ``"deprecated"`` sentinel for refresh_token, per #666) is
    treated by the SDK as a corruption marker and triggers
    ``destroyLocalState`` regardless of which OTHER keys are present.
    """
    body = _setter_js(PRIVY_TOKEN_LOCAL_STORAGE_KEY, jwt)
    if refresh_token:
        body += _setter_js(PRIVY_REFRESH_LOCAL_STORAGE_KEY, refresh_token)
    if privy_pat:
        body += _setter_js(PRIVY_PAT_LOCAL_STORAGE_KEY, privy_pat)
    if privy_id_token:
        body += _setter_js(PRIVY_ID_TOKEN_LOCAL_STORAGE_KEY, privy_id_token)
    return (
        "(function () {"
        "  try {"
        "    if (window.location && window.location.origin === "
        + json.dumps(_PROPHET_ORIGIN)
        + ") {"
        + body
        + "    }"
        "  } catch (e) {}"
        "})();"
    )
