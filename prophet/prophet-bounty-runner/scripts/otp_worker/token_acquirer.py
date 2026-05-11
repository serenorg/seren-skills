"""Server-side viewer-binding helpers.

Issue #487: browser-driven OTP and onboarding moved to the agent
(Seren Desktop's Playwright MCP). This module is now strictly the
post-OTP server-side surface — it takes a JWT the agent already
captured and binds it to a Prophet `viewer.user.id`.

Public surface (callers in `agent.py`):

  - `_query_viewer(*, gateway, jwt)` — returns `(viewer_id,
    viewer_email)`. Fails closed with `PrivyAuthFailed` if Prophet
    rejects the JWT (most commonly because no Prophet user record is
    bound to the Privy identity yet). The valid recovery is for the
    agent to re-run the browser-side modal stack (*Got it!* →
    referral code → *Skip* deposit) and re-export
    `PROPHET_SESSION_TOKEN`. The Python publisher proxy cannot create
    the user record because Prophet's `registerWithPrivy` mutation
    requires the full Privy cookie jar (`privy-token` +
    `privy-session` + `privy-refresh-token`), which the proxy does
    not forward.
"""

from __future__ import annotations

from typing import Any

from . import PrivyAuthFailed


def _query_viewer(*, gateway: Any, jwt: str) -> tuple[str, str]:
    """Call prophet-ai's `viewer { user { id email } }` and return (id, email).

    Fails closed with `PrivyAuthFailed` on any rejection. See the module
    docstring for why there is no server-side `registerWithPrivy`
    fallback.
    """
    try:
        result = gateway.call(
            "prophet-ai",
            "POST",
            "/api/graphql",
            body={
                "query": (
                    "query Viewer { viewer { user { id email } "
                    "walletBalance { availableCents totalCents "
                    "onChainUsdc safeAddress safeDeployed } } }"
                ),
                "variables": {},
            },
            # Phase-14 live probe (2026-05-08): Prophet's `Viewer`
            # type has no top-level `id`/`email`; user identity lives
            # at `viewer.user`. Wallet balance lives at
            # `viewer.walletBalance` and gets folded into the
            # participant_identity row so the deposit-recommendation
            # step has the data it needs.
            #
            # Auth: gateway needs SerenAPIKey on `Authorization` for
            # caller-auth/billing, so Privy JWT rides on the `Cookie`
            # header (`privy-token=<jwt>`) — the Prophet web app's
            # native auth channel and a documented passthrough header.
            headers={"Cookie": f"privy-token={jwt}"},
        )
    except Exception as exc:
        import os as _os
        import sys as _sys
        if _os.environ.get("PROPHET_BOUNTY_DEBUG_LOCAL_STORAGE") == "1":
            _sys.stderr.write(
                f"[diag] viewer_call_failed exc={exc!r} jwt_len={len(jwt)} "
                f"jwt_first16={jwt[:16]!r} jwt_dot_segments={jwt.count('.')}\n"
            )
        raise PrivyAuthFailed(f"viewer query failed: {exc}") from exc

    viewer = ((result or {}).get("data") or {}).get("viewer") or {}
    user = viewer.get("user") or {}
    viewer_id = user.get("id") or ""
    viewer_email = user.get("email") or ""
    if not viewer_id or not viewer_email:
        errors = (result or {}).get("errors") or []
        msg = errors[0].get("message", "viewer null") if errors else "viewer payload incomplete"
        raise PrivyAuthFailed(f"viewer payload empty: {msg}")
    return viewer_id, viewer_email
