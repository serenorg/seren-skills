"""Confidentiality labels and role-visibility map.

Every canonical object carries a `confidentiality_label`. Retrieval, routing,
and outbound execution MUST respect labels before content is surfaced or sent.

See family-office design doc, §5.9.
"""

from __future__ import annotations

from typing import Final

CONFIDENTIALITY_LABELS: Final[frozenset[str]] = frozenset(
    {
        "office-private",
        "principal-only",
        "legal-privileged",
        "tax-sensitive",
        "advisor-shareable",
        "execution-restricted",
    }
)

# Role-to-visible-labels map. Keys are caller_role values that audit_query
# accepts. Values are the labels that role may see. Unknown roles get the
# intersection — i.e., office-private only — as a safe default.
_ROLE_VISIBILITY: Final[dict[str, frozenset[str]]] = {
    "principal": frozenset(
        {
            "office-private",
            "principal-only",
            "legal-privileged",
            "tax-sensitive",
            "advisor-shareable",
        }
    ),
    "coo": frozenset(
        {
            "office-private",
            "tax-sensitive",
            "advisor-shareable",
        }
    ),
    "service_line_legal": frozenset(
        {
            "office-private",
            "legal-privileged",
            "advisor-shareable",
        }
    ),
    "service_line_tax": frozenset(
        {
            "office-private",
            "tax-sensitive",
            "advisor-shareable",
        }
    ),
    "advisor": frozenset({"advisor-shareable"}),
    "office_operator": frozenset({"office-private"}),
}

# execution-restricted is NEVER in a default visibility set. Reading it
# enqueues a review_item first (handled at a layer above audit_query).


class ConfidentialityError(Exception):
    """Raised when a confidentiality invariant is violated."""


def confidentiality_check(label: str) -> str:
    """Validate a label string. Return the canonical label or raise.

    Args:
        label: The label string to validate.

    Returns:
        The same label, if recognized.

    Raises:
        ConfidentialityError: If label is not in CONFIDENTIALITY_LABELS.
    """
    if label not in CONFIDENTIALITY_LABELS:
        raise ConfidentialityError(f"unknown confidentiality label: {label!r}")
    return label


def visible_labels_for_role(caller_role: str) -> frozenset[str]:
    """Return the set of confidentiality labels visible to a given role.

    Unknown roles get a safe default (office-private only). execution-restricted
    is never returned — a read of an execution-restricted object must be gated
    by an explicit review_item at a higher layer.
    """
    return _ROLE_VISIBILITY.get(caller_role, frozenset({"office-private"}))
