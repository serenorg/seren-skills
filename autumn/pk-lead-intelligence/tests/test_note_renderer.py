"""Locked-layout contract for the PK Lead enrichment Note.

The Note format is operator-reviewable and audit-load-bearing per
SKILL.md §"Privacy & Compliance" — every Note must surface its
enrichment timestamp, the sources used, and a short hypothesis so a
downstream reader can reconstruct *why* the Note says what it says.
This test pins those structural guarantees so a renderer change that
drops a section trips CI instead of shipping silently.

The test deliberately does **not** assert prose wording — that is the
operator's review surface, and the wording will iterate. The shape is
the contract.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.output import note_renderer
from scripts.research.claude_hypothesis import Hypothesis
from scripts.research.linkedin_search import LinkedInCandidate
from scripts.research.perplexity import PerplexityResearch
from scripts.sf.client import LeadRow


def _make_lead() -> LeadRow:
    return LeadRow(
        record_id="00Q5g00000ABCDEFGH",
        name="Jane Operator",
        source_url="https://example.lightning.force.com/lightning/o/Lead/list",
    )


def _make_perplexity() -> PerplexityResearch:
    return PerplexityResearch(
        summary="Acme Packaging recently announced Q1 capacity expansion.",
        citations=["https://example.com/a", "https://example.com/b"],
        raw_text="...",
    )


def _make_linkedin() -> LinkedInCandidate:
    return LinkedInCandidate(
        url="https://www.linkedin.com/in/jane-operator-abc/",
        title=None,
        match_confidence=80,
        reasons=["linkedin-profile-url", "name-tokens:jane,operator"],
    )


def _make_hypothesis() -> Hypothesis:
    return Hypothesis(
        text="Active capacity expansion at Acme implies PK pipeline budget.",
        recommended_action="Send the PK packaging deck and request 20 min next week.",
    )


def test_render_returns_locked_section_order() -> None:
    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=_make_perplexity(),
        linkedin=_make_linkedin(),
        hypothesis=_make_hypothesis(),
        now=datetime(2026, 5, 14, 10, 30, 0, tzinfo=timezone.utc),
    )

    headings = [s.heading for s in note.sections]
    assert headings == [
        "Lead",
        "Sources Used",
        "Research Summary",
        "LinkedIn",
        "Hypothesis",
        "Recommended Next Action",
    ]


def test_render_carries_audit_fields() -> None:
    """Per SKILL.md: timestamp + sources + hypothesis MUST be present."""

    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=_make_perplexity(),
        linkedin=_make_linkedin(),
        hypothesis=_make_hypothesis(),
        now=datetime(2026, 5, 14, 10, 30, 0, tzinfo=timezone.utc),
    )
    bodies = {s.heading: s.body for s in note.sections}

    # Timestamp lives on the envelope, not on a section.
    assert note.enriched_at_utc == "2026-05-14T10:30:00Z"
    # Sources Used must reference both research surfaces.
    assert "Perplexity" in bodies["Sources Used"]
    assert "LinkedIn" in bodies["Sources Used"]
    # Lead identity carries the record_id (the stable audit handle).
    assert "00Q5g00000ABCDEFGH" in bodies["Lead"]
    # Hypothesis body is non-empty.
    assert bodies["Hypothesis"].strip() != ""
    # Recommended action is non-empty.
    assert bodies["Recommended Next Action"].strip() != ""


def test_render_handles_missing_linkedin_match() -> None:
    """No LinkedIn candidate is a valid state — Note must still render."""

    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=_make_perplexity(),
        linkedin=None,
        hypothesis=_make_hypothesis(),
        now=datetime(2026, 5, 14, 10, 30, 0, tzinfo=timezone.utc),
    )
    bodies = {s.heading: s.body for s in note.sections}

    # The section is still present (locked layout) but flags absence.
    assert "LinkedIn" in {s.heading for s in note.sections}
    assert "none" in bodies["Sources Used"].lower()


def test_render_default_clock_produces_utc_timestamp() -> None:
    """When `now` is omitted, the renderer must default to UTC.

    Avoids a class of bug where a local-time renderer ships Notes whose
    audit timestamps disagree with the SerenDB ledger (UTC).
    """

    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=_make_perplexity(),
        linkedin=None,
        hypothesis=_make_hypothesis(),
    )
    # ISO-8601 UTC suffix.
    assert note.enriched_at_utc.endswith("Z")
