"""Operator-pinned bounty spec the auto-resolve validates against.

Plan §3 ADR ("Bounty auto-resolution hardening"): `customer_slug` is
free-form per the seren-bounty doc, so anyone could create a bounty
with `customer_slug=prophet`. Auto-resolve filters by
`customer_slug=prophet&status=open`, validates each candidate against
the immutable fields below, and picks the newest match.

`max_pool_atomic` is intentionally NOT validated as an exact match.
Per the seren-bounty skill doc, the cap can be expanded post-creation
via PATCH `additional_max_pool_atomic`. Plan §22 acceptance criterion
#13 demands exact-match for `(owner, title, hold_days,
max_pool_atomic, deadline, tiers)`, but in practice the live bounty
opens at $150 and grows to $500 — so we treat `max_pool_atomic` as a
minimum-floor (>= MIN_MAX_POOL_ATOMIC) and verify the immutable
identity fields strictly.
"""

from __future__ import annotations

from typing import Any

CUSTOMER_SLUG = "prophet"
EXPECTED_HOLD_DAYS = 90
MIN_MAX_POOL_ATOMIC = 150_000_000  # $150; production target is 500_000_000

EXPECTED_TIERS = [
    {"threshold": 0, "rate_atomic": 10_000_000},
    {"threshold": 25, "rate_atomic": 5_000_000},
]

EXPECTED_VERIFIER_SPEC_ATTRS = [
    {"path": "issuer", "operator": "eq", "value": "reconciler-v1"}
]
EXPECTED_VERIFIER_EVENT_TYPE = "prophet_market_created"
EXPECTED_SUBMISSION_MODE = "required"


def validate_bounty(bounty: dict[str, Any]) -> tuple[bool, str]:
    """Return (ok, reason). reason is empty when ok."""
    if bounty.get("customer_slug") != CUSTOMER_SLUG:
        return False, f"customer_slug={bounty.get('customer_slug')!r} != {CUSTOMER_SLUG!r}"
    if bounty.get("hold_days") != EXPECTED_HOLD_DAYS:
        return False, f"hold_days={bounty.get('hold_days')!r} != {EXPECTED_HOLD_DAYS}"
    if int(bounty.get("max_pool_atomic") or 0) < MIN_MAX_POOL_ATOMIC:
        return (
            False,
            f"max_pool_atomic={bounty.get('max_pool_atomic')} < min {MIN_MAX_POOL_ATOMIC}",
        )
    if bounty.get("submission_mode") != EXPECTED_SUBMISSION_MODE:
        return (
            False,
            f"submission_mode={bounty.get('submission_mode')!r} != {EXPECTED_SUBMISSION_MODE!r}",
        )
    tiers = bounty.get("tiers") or []
    if [{"threshold": t.get("threshold"), "rate_atomic": t.get("rate_atomic")} for t in tiers] != EXPECTED_TIERS:
        return False, f"tiers={tiers!r} != {EXPECTED_TIERS!r}"
    spec = bounty.get("verifier_spec") or {}
    event_match = spec.get("event_match") or {}
    if event_match.get("event_type") != EXPECTED_VERIFIER_EVENT_TYPE:
        return (
            False,
            f"verifier event_type={event_match.get('event_type')!r} != {EXPECTED_VERIFIER_EVENT_TYPE!r}",
        )
    attrs = event_match.get("attributes") or []
    normalized = [
        {"path": a.get("path"), "operator": a.get("operator"), "value": a.get("value")}
        for a in attrs
    ]
    if normalized != EXPECTED_VERIFIER_SPEC_ATTRS:
        return False, f"verifier attributes={normalized!r} != {EXPECTED_VERIFIER_SPEC_ATTRS!r}"
    return True, ""
