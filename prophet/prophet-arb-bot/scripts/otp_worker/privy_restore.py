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

The Privy SDK uses two persistence shapes:

  - Plain string values (`privy:token`, `privy:refresh_token`,
    `privy:caid`, `privy:<app_id>:recent-login-method`) are stored as
    `JSON.stringify(value)`, i.e. the bare string wrapped in literal
    surrounding double quotes. The SDK strips that wrapping via
    `localStorage.getItem(k).slice(1, -1)`.
  - JSON values (`privy:connections` is a JSON array of wallet
    connections) are stored as the JSON itself, with no extra outer
    wrapping. `capture_artifacts` reads them verbatim and the restore
    helper plants them verbatim.

Issue #676: roll back the privy:pat / privy:id_token contract added by
PR #675 (#674). Those keys do not exist on Prophet's live Privy session
(verified by manual MCP walk-through, 2026-05-18). Capture and plant
`privy:connections` (the embedded wallet `/create` signs with),
`privy:caid`, and `privy:<app_id>:recent-login-method` instead.
"""

from __future__ import annotations

import json
from typing import Any

from .playwright_client import (
    PROPHET_APP_URL,
    PRIVY_CAID_LOCAL_STORAGE_KEY,
    PRIVY_CONNECTIONS_LOCAL_STORAGE_KEY,
    PRIVY_RECENT_LOGIN_METHOD_LOCAL_STORAGE_KEY,
    PRIVY_REFRESH_LOCAL_STORAGE_KEY,
    PRIVY_TOKEN_LOCAL_STORAGE_KEY,
)

_PROPHET_ORIGIN = "https://app.prophetmarket.ai"


def restore_privy_session(
    session: Any,
    *,
    jwt: str,
    refresh_token: str,
    privy_connections: str = "",
    privy_caid: str = "",
    privy_recent_login_method: str = "",
    privy_session_cookie: str = "",
    privy_token_cookie: str = "",
) -> None:
    """Plant Privy session state into the caller's browser, then navigate.

    Registers a `document_start` init script that writes the Privy
    state into ``localStorage`` on the Prophet origin, then navigates
    once. The init script persists for the lifetime of the browser
    context, so subsequent navigations (e.g. to ``/create``) also see
    the planted state.

    Issue #676: ``privy:connections`` carries the embedded wallet
    metadata Prophet's ``/create`` flow uses to sign
    ``createMarketWithBet``. Without it, the SDK boots without a
    signer and the cycle either bounces to ``/?returnTo=/create`` or
    stalls at the in-browser signing prompt.

    Issue #666: ``privy:refresh_token`` was retired server-side. An
    empty ``refresh_token`` (the post-#666 cache shape) is fine; we
    just skip that setter rather than planting an empty string that
    the SDK would treat as a corruption marker.

    Issue #705: also plant the ``privy-session`` HTTP cookie when the
    cache carries it. localStorage planting alone is enough for the
    Privy *client* SDK to recognize a session, but Prophet's server-
    side middleware checks the HttpOnly ``privy-session`` cookie when
    deciding whether to serve ``/create`` vs redirect to
    ``/?returnTo=/create``. Without the cookie restore, the warm
    context was effectively unauthenticated server-side every cycle
    — the bot would land on the homepage, the homepage's quick-create
    textarea would satisfy ``wait_for(question_input)``, and the
    Create Market click would never fire the AI calc. Verified by the
    #704 page_url diagnostic: ``page_url`` came back as
    ``/?returnTo=%2Fcreate`` on every blocked entry.
    """
    if not jwt:
        raise ValueError("restore_privy_session requires jwt")

    cookies: list[dict[str, Any]] = []
    if privy_session_cookie:
        cookies.append(_privy_session_cookie_payload(privy_session_cookie))
    if privy_token_cookie:
        # Issue #707: also plant the JWT-bearing `privy-token` cookie.
        # Prophet's middleware checks BOTH cookies; #706 restored only
        # privy-session and the page still landed on /?returnTo=/create.
        cookies.append(_privy_token_cookie_payload(privy_token_cookie))
    if cookies:
        session.add_cookies(cookies)

    script = _build_init_script(
        jwt=jwt,
        refresh_token=refresh_token,
        privy_connections=privy_connections,
        privy_caid=privy_caid,
        privy_recent_login_method=privy_recent_login_method,
    )
    session.add_init_script(script)
    session.navigate(PROPHET_APP_URL)


def _privy_token_cookie_payload(value: str) -> dict[str, Any]:
    """Cookie payload for the ``privy-token`` cookie.

    Issue #707: same attributes as ``privy-session`` (HttpOnly, Secure,
    Lax, on the Prophet origin) — Privy SDK writes both cookies with
    identical security flags at login time.
    """
    return {
        "name": "privy-token",
        "value": value,
        "domain": "app.prophetmarket.ai",
        "path": "/",
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    }


def _privy_session_cookie_payload(value: str) -> dict[str, Any]:
    """Cookie payload Playwright's ``BrowserContext.addCookies`` expects.

    Issue #705: the captured cookie is HttpOnly+Secure and bound to the
    Prophet origin. Lowercased ``samesite`` ('lax') matches what Privy
    set at login time (verified via ``session.get_cookie`` in the
    capture path). Path '/' is the Privy convention.
    """
    return {
        "name": "privy-session",
        "value": value,
        "domain": "app.prophetmarket.ai",
        "path": "/",
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    }


def _setter_js_string(key: str, value: str) -> str:
    """Plant a Privy *string* value: SDK stores ``JSON.stringify(value)``.

    The double-encoding (`json.dumps(json.dumps(value))`) keeps the
    injection safe — a malicious cache value cannot escape its own
    quotes.
    """
    wrapped = json.dumps(value)
    return (
        "      window.localStorage.setItem("
        + json.dumps(key)
        + ", "
        + json.dumps(wrapped)
        + ");"
    )


def _setter_js_raw_json(key: str, raw_json: str) -> str:
    """Plant a Privy *raw-JSON* value (e.g. ``privy:connections`` array).

    ``capture_artifacts`` reads the localStorage value verbatim for
    JSON-array keys, so the cache already carries the canonical
    serialization. We plant that string as-is — no additional
    ``JSON.stringify`` wrapping — because the SDK reads these keys
    with ``JSON.parse`` directly.
    """
    return (
        "      window.localStorage.setItem("
        + json.dumps(key)
        + ", "
        + json.dumps(raw_json)
        + ");"
    )


def _build_init_script(
    *,
    jwt: str,
    refresh_token: str,
    privy_connections: str = "",
    privy_caid: str = "",
    privy_recent_login_method: str = "",
) -> str:
    """Build the JS that plants Privy state at ``document_start``.

    The origin guard is paranoia: the Python-owned browser only ever
    navigates to Prophet in this flow, but ``add_init_script`` fires
    for ``about:blank`` and any subframes too, and there is no reason
    to leak Privy tokens to those origins.

    Issue #710: ``privy:refresh_token`` is always planted, with the
    literal ``"deprecated"`` sentinel when the cached refresh_token is
    empty. Post-migration the Privy SDK itself writes that exact
    sentinel into ``privy:refresh_token`` at login, and reads it on
    every boot as the "post-migration JWT-only session" marker. The
    pre-#710 restore skipped the key when refresh_token was empty;
    Privy then read ``null`` from localStorage, decided the session
    was corrupt, called ``destroyLocalState``, and tore down the JWT
    and connections we'd just planted — which is why every restored
    cycle landed on ``/?returnTo=%2Fcreate``. Capture and cache still
    normalize ``"deprecated"`` to empty so the on-disk JSON stays
    clean (#666); restore here is what reverses the normalization so
    the planted state matches what the live SDK writes.

    The remaining Privy keys (``privy:connections``, ``privy:caid``,
    ``privy:<app_id>:recent-login-method``) are still planted only
    when we have a non-empty value for them. Per the #674 diagnostics,
    planting an empty ``privy:connections`` triggers a different
    corruption path, so those gates stay in place.
    """
    body = _setter_js_string(PRIVY_TOKEN_LOCAL_STORAGE_KEY, jwt)
    body += _setter_js_string(
        PRIVY_REFRESH_LOCAL_STORAGE_KEY,
        refresh_token or "deprecated",
    )
    if privy_connections:
        body += _setter_js_raw_json(
            PRIVY_CONNECTIONS_LOCAL_STORAGE_KEY, privy_connections
        )
    if privy_caid:
        body += _setter_js_string(PRIVY_CAID_LOCAL_STORAGE_KEY, privy_caid)
    if privy_recent_login_method:
        body += _setter_js_string(
            PRIVY_RECENT_LOGIN_METHOD_LOCAL_STORAGE_KEY,
            privy_recent_login_method,
        )
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
