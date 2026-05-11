"""Bind a Prophet user to the AGENTACCESS affiliate code.

Discovered via live GraphQL introspection on 2026-05-08:

    Mutation: submitReferralCode(code: String!) -> <result>
    Companion: dismissReferralPrompt() (Prophet webapp uses this when
               a user skips the referral prompt; the skill never calls
               it because we always bind to AGENTACCESS)

The bind is per-user and one-time. After it lands, every market the
user creates is automatically attributed to AGENTACCESS on Prophet's
affiliate-scoped APIs — which is what the operator-side reconciler
queries to find qualifying markets and credit earnings.

Without this call the operator's reconciler returns zero markets per
run, so every bounty earning would silently never accrue. Plumbing it
into the cold-start onboarding flow closes that gap.
"""

from __future__ import annotations

from typing import Any

# Hardcoded as a constant (not config). The skill is pinned to this
# customer's affiliate code; the bounty itself is owned by the same org.
AGENTACCESS_REFERRAL_CODE = "AGENTACCESS"

_SUBMIT_REFERRAL_CODE_MUTATION = (
    "mutation SubmitReferralCode($code: String!) { "
    "submitReferralCode(code: $code) { __typename } "
    "}"
)


def bind_agentaccess(*, gateway: Any, jwt: str) -> None:
    """Idempotent submitReferralCode(AGENTACCESS) call.

    Re-runs after a successful first bind get an "already redeemed"-
    style error from Prophet which is treated as success. Auth or
    schema-drift errors propagate so the caller can surface them to
    the run row and, in the operator's case, alert.
    """
    result = gateway.call(
        "prophet-ai",
        "POST",
        "/api/graphql",
        body={
            "query": _SUBMIT_REFERRAL_CODE_MUTATION,
            "variables": {"code": AGENTACCESS_REFERRAL_CODE},
        },
        # Same auth shape as the rest of the prophet-ai gateway calls:
        # SerenAPIKey on Authorization (gateway-side, applied by the
        # publisher proxy), Privy JWT on Cookie. See
        # otp_worker/token_acquirer.py::_query_viewer for the contract.
        headers={"Cookie": f"privy-token={jwt}"},
    )
    errors = (result or {}).get("errors") or []
    if not errors:
        return
    msg = (errors[0].get("message") or "").lower()
    # Prophet wording can vary; any of these phrasings means the bind
    # is in place from a prior run and the caller can proceed.
    if any(token in msg for token in ("already", "duplicate", "exists", "redeemed")):
        return
    raise RuntimeError(f"submitReferralCode failed: {errors!r}")
