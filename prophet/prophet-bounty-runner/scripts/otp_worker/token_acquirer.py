"""Server-side viewer-binding helpers.

Issue #487: browser-driven OTP and onboarding moved to the agent
(Seren Desktop's Playwright MCP). This module is now strictly the
post-OTP server-side surface — it takes a JWT the agent already
captured and binds it to a Prophet `viewer.user.id`.

Issue #493: dropped the `prophet-ai` Seren publisher hop and now talk
to Prophet directly via `prophet.transport.ProphetDirectTransport`.
Live evidence showed the gateway's `Authorization` slot is reserved
for SEREN_API_KEY billing auth, so the Privy JWT could not ride that
slot through the proxy; the previous `Cookie: privy-token=*` workaround
was a dead end because Prophet ignores cookies for viewer-binding.

Public surface (callers in `agent.py`):

  - `_query_viewer(*, transport, jwt)` — returns `(viewer_id,
    viewer_email)`. Fails closed with `PrivyAuthFailed` if Prophet
    rejects the JWT (most commonly because no Prophet user record is
    bound to the Privy identity yet). The valid recovery is for the
    agent to re-run the browser-side modal stack (*Got it!* →
    referral code → *Skip* deposit) and re-export
    `PROPHET_SESSION_TOKEN`.
"""

from __future__ import annotations

from typing import Any

from . import PrivyAuthFailed


_VIEWER_QUERY = (
    "query Viewer { viewer { user { id email } "
    "walletBalance { availableCents totalCents "
    "onChainUsdc safeAddress safeDeployed } } }"
)


def _query_viewer(*, transport: Any, jwt: str) -> tuple[str, str]:
    """Call Prophet's `viewer { user { id email } }` and return (id, email).

    Fails closed with `PrivyAuthFailed` on any rejection. See the module
    docstring for why there is no server-side `registerWithPrivy`
    fallback.
    """
    try:
        # Prophet's `Viewer` type has no top-level `id`/`email`; user
        # identity lives at `viewer.user`. Wallet balance is folded into
        # the participant_identity row for the deposit-recommendation
        # step (#467). The transport carries the Privy JWT on
        # `Authorization: Bearer ...` directly to app.prophetmarket.ai
        # — no publisher hop, no Cookie path.
        result = transport.post_graphql(
            jwt=jwt, query=_VIEWER_QUERY, variables={}
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
    # Issue #518: wallet-only Prophet accounts (MetaMask, WalletConnect)
    # have no email; bind on viewer_id alone. The bounty reconciler
    # attributes by creator.id, not email.
    if not viewer_id:
        errors = (result or {}).get("errors") or []
        msg = errors[0].get("message", "viewer null") if errors else "viewer payload incomplete"
        raise PrivyAuthFailed(f"viewer payload empty: {msg}")
    return viewer_id, viewer_email
