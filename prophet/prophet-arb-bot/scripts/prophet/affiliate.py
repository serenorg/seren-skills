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


def bind_agentaccess(*, transport: Any, jwt: str) -> None:
    """Idempotent submitReferralCode(AGENTACCESS) call.

    Re-runs after a successful first bind get an "already redeemed"-
    style error from Prophet which is treated as success. Auth or
    schema-drift errors propagate so the caller can surface them to
    the run row and, in the operator's case, alert.

    Issue #493: routes directly to `app.prophetmarket.ai` via
    `ProphetDirectTransport`. The previous `gateway.call("prophet-ai",
    ...)` path is gone because the Seren publisher proxy reserves the
    `Authorization` header for SEREN_API_KEY and Prophet ignores the
    `Cookie: privy-token=*` workaround.
    """
    from . import ProphetGraphQLError

    try:
        transport.post_graphql(
            jwt=jwt,
            query=_SUBMIT_REFERRAL_CODE_MUTATION,
            variables={"code": AGENTACCESS_REFERRAL_CODE},
            operation_name="SubmitReferralCode",
        )
    except ProphetGraphQLError as exc:
        # Prophet wording can vary; any of these phrasings means the bind
        # is in place from a prior run and the caller can proceed.
        msg = str(exc).lower()
        if any(token in msg for token in ("already", "duplicate", "exists", "redeemed")):
            return
        raise RuntimeError(f"submitReferralCode failed: {exc}") from exc
