"""Post-drafting validators for outbound affiliate messages.

Issue #404 — defense-in-depth. Every merged outbound body must contain the
exact `tracked_link` (partner_link_url) that was bootstrapped from
seren-affiliates. A hallucinated URL, a stripped placeholder, or a stale code
would otherwise reach the recipient undetected.
"""

from __future__ import annotations


def validate_tracked_link(
    *,
    merged_body: str,
    tracked_link: str,
) -> dict:
    """Assert the bootstrapped tracked_link substring is present in merged_body.

    Returns {"status": "ok"} on success, or
    {"status": "validation_failed", "error_code": "tracked_link_missing", ...}
    so the send pipeline can fail-closed.
    """
    if not tracked_link:
        return {
            "status": "validation_failed",
            "error_code": "tracked_link_empty",
            "message": (
                "Bootstrapped tracked_link is empty. Refusing to send a draft "
                "whose partner_link cannot be verified."
            ),
        }
    if tracked_link not in merged_body:
        return {
            "status": "validation_failed",
            "error_code": "tracked_link_missing",
            "message": (
                "Merged draft body does not contain the bootstrapped "
                "tracked_link. Fail-closed per #404."
            ),
            "expected_tracked_link": tracked_link,
        }
    return {"status": "ok"}
