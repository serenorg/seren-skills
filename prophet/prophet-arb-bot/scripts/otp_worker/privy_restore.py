"""Restore a cached Privy session into a fresh Python-owned Playwright browser.

Issue #636: the `create-market-via-ui` path needs to land on
`https://app.prophetmarket.ai/create` already authenticated, without
driving the OTP modal. Since #583 Privy persists both `privy:token` and
`privy:refresh_token` to `window.localStorage` (JSON-stringified), so the
agent can warm-start the browser by writing those keys into the Prophet
origin's localStorage before navigating onward.

`window.localStorage` is partitioned by origin, so the caller MUST land on
`https://app.prophetmarket.ai` first. After this function returns, the
caller can navigate to `/create` and the Privy SDK will pick the session
up exactly as if the user had just refreshed the tab.
"""

from __future__ import annotations

import json
from typing import Any

from .playwright_client import (
    PROPHET_APP_URL,
    PRIVY_REFRESH_LOCAL_STORAGE_KEY,
    PRIVY_TOKEN_LOCAL_STORAGE_KEY,
)


def restore_privy_session(session: Any, *, jwt: str, refresh_token: str) -> None:
    """Write `privy:token` and `privy:refresh_token` into the Prophet origin.

    Privy's web SDK reads each value via `localStorage.getItem(key)` then
    strips a balanced pair of surrounding double-quotes (it serializes with
    `JSON.stringify`). We mirror that wrap exactly so the SDK can unwrap.
    """
    if not jwt or not refresh_token:
        raise ValueError("restore_privy_session requires both jwt and refresh_token")

    session.navigate(PROPHET_APP_URL)
    _write_local_storage(session, key=PRIVY_TOKEN_LOCAL_STORAGE_KEY, value=jwt)
    _write_local_storage(
        session, key=PRIVY_REFRESH_LOCAL_STORAGE_KEY, value=refresh_token
    )


def _write_local_storage(session: Any, *, key: str, value: str) -> None:
    wrapped = json.dumps(value)
    # `wrapped` already includes the surrounding double-quotes the Privy
    # SDK expects (`'"<jwt>"'`). Embed it as a JSON string literal so the
    # eval'd JS sees the same quoted form via `JSON.parse`.
    script = (
        "(() => { window.localStorage.setItem("
        + json.dumps(key)
        + ", "
        + json.dumps(wrapped)
        + "); return true; })()"
    )
    evaluate = getattr(session, "evaluate", None)
    if callable(evaluate):
        evaluate(script)
        return
    # Real `RealBrowserSession` doesn't expose `evaluate` on the Protocol but
    # ships one via the underlying MCP `/evaluate` call.
    session._call("/evaluate", {"script": script})  # type: ignore[attr-defined]
