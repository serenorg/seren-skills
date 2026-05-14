"""Match-confidence scoring contract for LinkedIn discovery.

Issue #530 explicitly calls out "LinkedIn match-confidence scoring" as
a TDD target. This test pins the scoring behavior:

- Profile-URL anchor is required (non-/in/ URLs score 0).
- Name-token matches scale the score.
- Company-hint matches add a single bounded boost.
- Score is capped at 100.

Scoring is a pure function, so all tests run with no network and no
adapter wiring.
"""

from __future__ import annotations

from scripts.research import linkedin_search


def test_score_rejects_non_profile_url() -> None:
    score, reasons = linkedin_search.score_candidate(
        candidate_url="https://www.linkedin.com/company/acme/",
        candidate_title="Acme Packaging",
        candidate_snippet="Jane Operator works at Acme Packaging",
        lead_name="Jane Operator",
        company_hint="Acme Packaging",
    )
    assert score == 0
    assert "url-not-profile" in reasons


def test_score_full_name_match_with_company_hint() -> None:
    score, reasons = linkedin_search.score_candidate(
        candidate_url="https://www.linkedin.com/in/jane-operator-abc/",
        candidate_title="Jane Operator | Acme Packaging",
        candidate_snippet="VP Sourcing at Acme Packaging in Cleveland.",
        lead_name="Jane Operator",
        company_hint="Acme Packaging",
    )
    # 20 (profile anchor) + 50 (full name) + 10 (company hit) = 80
    assert score == 80
    assert any(r.startswith("name-tokens:") for r in reasons)
    assert any(r.startswith("company-token:") for r in reasons)


def test_score_partial_name_match_no_company() -> None:
    """Half of the name tokens match → half of the 50-point name pool."""

    score, _ = linkedin_search.score_candidate(
        candidate_url="https://www.linkedin.com/in/some-profile/",
        candidate_title=None,
        candidate_snippet="Jane focuses on procurement.",
        lead_name="Jane Operator",
        company_hint=None,
    )
    # 20 (profile anchor) + 25 (1 of 2 tokens) = 45
    assert score == 45


def test_score_capped_at_100() -> None:
    """Repeated company tokens cannot push the score past the cap."""

    score, _ = linkedin_search.score_candidate(
        candidate_url="https://www.linkedin.com/in/jane-operator/",
        candidate_title="Jane Operator Acme Acme Acme",
        candidate_snippet="Jane Operator Acme Acme Acme Acme",
        lead_name="Jane Operator",
        company_hint="Acme",
    )
    assert score <= 100


def test_score_handles_empty_name_tokens() -> None:
    """Empty / punctuation-only lead name does not blow up."""

    score, _ = linkedin_search.score_candidate(
        candidate_url="https://www.linkedin.com/in/x/",
        candidate_title=None,
        candidate_snippet="",
        lead_name="???",
        company_hint=None,
    )
    # 20 (profile anchor), no name signal possible.
    assert score == 20
