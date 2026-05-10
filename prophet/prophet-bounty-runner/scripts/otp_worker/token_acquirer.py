"""Server-side viewer-binding helpers.

Issue #487: browser-driven OTP and onboarding moved to the agent
(Seren Desktop's Playwright MCP). This module is now strictly the
post-OTP server-side surface — it takes a JWT the agent already
captured and binds it to a Prophet `viewer.user.id`.

Public surface (callers in `agent.py`):

  - `_query_viewer(*, gateway, jwt, email)` — returns `(viewer_id,
    viewer_email)`. Falls back to `_register_with_privy` once if
    Prophet rejects the JWT with `_ViewerNotAuthorized` (no Prophet
    user record bound yet).

  - `_register_with_privy(*, gateway, jwt, email)` — idempotent
    `registerWithPrivy` mutation; live publisher proxy currently
    rejects this with `Privy authentication required` (issue #487
    out-of-scope note), so the fallback rarely succeeds today. The
    agent's modal stack handles the user-creation path until Prophet
    fixes the publisher-proxy auth shape.
"""

from __future__ import annotations

from typing import Any

from . import PrivyAuthFailed


class _ViewerNotAuthorized(Exception):
    """Raised when Prophet rejects the viewer query (401 / no user record)."""


def _query_viewer(*, gateway: Any, jwt: str, email: str = "") -> tuple[str, str]:
    """Call prophet-ai's `viewer { id email }` and return (id, email).

    If the first viewer call fails (most commonly because the user
    has a Privy identity but no Prophet user record bound to it),
    try `registerWithPrivy` once and re-query. Idempotent on the
    Prophet side: an already-registered user gets back their existing
    record, and a fresh user gets created. Either way the viewer
    query should succeed on retry.
    """
    try:
        return _viewer_call(gateway, jwt)
    except _ViewerNotAuthorized as exc:
        if not email:
            raise PrivyAuthFailed(f"viewer query failed: {exc}") from exc
        try:
            _register_with_privy(gateway=gateway, jwt=jwt, email=email)
        except Exception as register_exc:
            raise PrivyAuthFailed(
                f"viewer query failed: {exc}; registerWithPrivy fallback also failed: {register_exc}"
            ) from exc
        try:
            return _viewer_call(gateway, jwt)
        except Exception as retry_exc:
            raise PrivyAuthFailed(
                f"viewer query still failed after registerWithPrivy: {retry_exc}"
            ) from retry_exc


def _viewer_call(gateway: Any, jwt: str) -> tuple[str, str]:
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
        raise _ViewerNotAuthorized(str(exc)) from exc

    viewer = ((result or {}).get("data") or {}).get("viewer") or {}
    user = viewer.get("user") or {}
    viewer_id = user.get("id") or ""
    viewer_email = user.get("email") or ""
    if not viewer_id or not viewer_email:
        errors = (result or {}).get("errors") or []
        msg = errors[0].get("message", "viewer null") if errors else "viewer payload incomplete"
        raise _ViewerNotAuthorized(f"viewer payload empty: {msg}")
    return viewer_id, viewer_email


def _register_with_privy(*, gateway: Any, jwt: str, email: str) -> None:
    """Idempotent registerWithPrivy call. Errors propagate to caller.

    Phase-14: discovered via live introspection on 2026-05-08. Input
    fields are all SCALAR (email, fullName, eoaAddress, isWalletLogin),
    none NON_NULL, so passing only email is safe. Prophet-side schema
    is `Mutation: registerWithPrivy(input: RegisterWithPrivyInput!) ->
    PrivyRegistrationResult!`.

    Issue #487 out-of-scope note: the live publisher proxy rejects
    this mutation with `Privy authentication required` even when the
    JWT successfully passes a `viewer` query. New-user creation
    happens via the agent's modal stack until Prophet fixes the
    proxy auth shape.
    """
    import os as _os
    import sys as _sys
    mutation = (
        "mutation RegisterWithPrivy($input: RegisterWithPrivyInput!) { "
        "registerWithPrivy(input: $input) { __typename } "
        "}"
    )
    result = gateway.call(
        "prophet-ai",
        "POST",
        "/api/graphql",
        body={
            "query": mutation,
            "variables": {"input": {"email": email, "isWalletLogin": False}},
        },
        headers={"Cookie": f"privy-token={jwt}"},
    )
    if _os.environ.get("PROPHET_BOUNTY_DEBUG_LOCAL_STORAGE") == "1":
        errors = (result or {}).get("errors") or []
        _sys.stderr.write(
            f"[diag] registerWithPrivy result.data={(result or {}).get('data')!r} "
            f"errors={errors!r}\n"
        )
