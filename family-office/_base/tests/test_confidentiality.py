"""Critical tests for confidentiality labels and role visibility."""

from __future__ import annotations

import pytest

from family_office_base.confidentiality import (
    CONFIDENTIALITY_LABELS,
    ConfidentialityError,
    confidentiality_check,
    visible_labels_for_role,
)


def test_all_six_labels_defined() -> None:
    assert CONFIDENTIALITY_LABELS == frozenset(
        {
            "office-private",
            "principal-only",
            "legal-privileged",
            "tax-sensitive",
            "advisor-shareable",
            "execution-restricted",
        }
    )


def test_confidentiality_check_rejects_unknown_label() -> None:
    with pytest.raises(ConfidentialityError, match="unknown"):
        confidentiality_check("top-secret")


def test_confidentiality_check_accepts_known_label() -> None:
    assert confidentiality_check("principal-only") == "principal-only"


def test_principal_sees_more_than_coo() -> None:
    principal = visible_labels_for_role("principal")
    coo = visible_labels_for_role("coo")
    # principal must see at least what COO sees, plus strictly more.
    assert coo <= principal
    assert principal > coo


def test_coo_cannot_see_principal_only_or_legal_privileged() -> None:
    coo = visible_labels_for_role("coo")
    assert "principal-only" not in coo
    assert "legal-privileged" not in coo


def test_advisor_sees_only_advisor_shareable() -> None:
    assert visible_labels_for_role("advisor") == frozenset({"advisor-shareable"})


def test_unknown_role_gets_safe_default_only() -> None:
    assert visible_labels_for_role("ghost") == frozenset({"office-private"})


def test_execution_restricted_is_never_in_any_default_visibility() -> None:
    for role in (
        "principal",
        "coo",
        "service_line_legal",
        "service_line_tax",
        "advisor",
        "office_operator",
        "ghost",
    ):
        assert "execution-restricted" not in visible_labels_for_role(role), (
            f"execution-restricted leaked into {role!r} default visibility"
        )
