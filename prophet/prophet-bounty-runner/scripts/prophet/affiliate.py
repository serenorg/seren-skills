"""Bind a Prophet user to the AGENTACCESS affiliate code.

The browser runbook still fills the referral modal when Prophet shows it,
but the Python validation path also enforces the bind through Prophet's
server-side mutation. This keeps bounty attribution from silently relying
on a UI step that may have been skipped or already dismissed.
"""

from __future__ import annotations

from typing import Any

AGENTACCESS_REFERRAL_CODE = "AGENTACCESS"

_SUBMIT_REFERRAL_CODE_MUTATION = (
    "mutation SubmitReferralCode($code: String!) { "
    "submitReferralCode(code: $code) { __typename } "
    "}"
)


def bind_agentaccess(*, transport: Any, jwt: str) -> None:
    """Idempotently submit Prophet referral code AGENTACCESS."""
    from . import ProphetGraphQLError

    try:
        transport.post_graphql(
            jwt=jwt,
            query=_SUBMIT_REFERRAL_CODE_MUTATION,
            variables={"code": AGENTACCESS_REFERRAL_CODE},
            operation_name="SubmitReferralCode",
        )
    except ProphetGraphQLError as exc:
        msg = str(exc).lower()
        if any(token in msg for token in ("already", "duplicate", "exists", "redeemed")):
            return
        raise RuntimeError(f"submitReferralCode failed: {exc}") from exc
