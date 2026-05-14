"""Pure renderer for the canonical Lead Note format.

Section-by-section rendering of the locked Note layout that the
operator signs off on at the end of phase 2. Pure function — does not
talk to Salesforce or external services and does not perform I/O.

The layout is locked: section order, section presence, and the
required audit fields (timestamp, sources, hypothesis, recommended
action) are pinned by `tests/test_note_renderer.py`. Editing this
file without updating that test is a contract violation per SKILL.md
"Privacy & Compliance".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from scripts.research.claude_hypothesis import Hypothesis
from scripts.research.linkedin_search import LinkedInCandidate
from scripts.research.perplexity import PerplexityResearch
from scripts.sf.client import LeadRow


# --------------------------------------------------------------------- #
# Output types                                                          #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class NoteSection:
    """One headed section of the rendered Note."""

    heading: str
    body: str


@dataclass(frozen=True)
class RenderedNote:
    """The full rendered Note ready for serialization (e.g. to .docx).

    `enriched_at_utc` is an ISO-8601 UTC timestamp string with a
    trailing `Z`. Storing it as a string on the envelope (not inside
    a section) keeps the audit timestamp easy to read out of the
    SerenDB ledger without parsing the section bodies.
    """

    title: str
    sections: list[NoteSection]
    enriched_at_utc: str


# --------------------------------------------------------------------- #
# Locked section heading constants                                      #
# --------------------------------------------------------------------- #

# Order is load-bearing — section_order on the rendered output must
# match this tuple. Changes here are a contract change; update
# tests/test_note_renderer.py in the same commit.
_LOCKED_ORDER = (
    "Lead",
    "Sources Used",
    "Research Summary",
    "LinkedIn",
    "Hypothesis",
    "Recommended Next Action",
)


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


def render(
    *,
    lead: LeadRow,
    perplexity: PerplexityResearch,
    linkedin: Optional[LinkedInCandidate],
    hypothesis: Hypothesis,
    now: Optional[datetime] = None,
) -> RenderedNote:
    """Build the locked-layout `RenderedNote` for one enrichment.

    `now` defaults to `datetime.now(timezone.utc)`. Callers in production
    let it default; tests pin a known timestamp so the audit-field
    assertion is deterministic.
    """

    when = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        # Be defensive — a naive datetime would silently slip a
        # local-time timestamp into a Note whose audit fields are
        # documented as UTC.
        when = when.replace(tzinfo=timezone.utc)
    when_str = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lead_body = (
        f"Name: {lead.name}\n"
        f"Record ID: {lead.record_id}\n"
        f"Source: {lead.source_url}"
    )

    sources_lines = [f"Perplexity citations: {len(perplexity.citations)}"]
    if linkedin is not None:
        sources_lines.append(
            f"LinkedIn match: {linkedin.url} ({linkedin.match_confidence}%)"
        )
    else:
        sources_lines.append("LinkedIn match: none")
    sources_body = "\n".join(sources_lines)

    summary_body = perplexity.summary or "(empty)"

    if linkedin is not None:
        reasons_str = ", ".join(linkedin.reasons) or "none"
        linkedin_body = (
            f"URL: {linkedin.url}\n"
            f"Match confidence: {linkedin.match_confidence}\n"
            f"Reasons: {reasons_str}"
        )
    else:
        linkedin_body = "No LinkedIn candidate found."

    bodies = {
        "Lead": lead_body,
        "Sources Used": sources_body,
        "Research Summary": summary_body,
        "LinkedIn": linkedin_body,
        "Hypothesis": hypothesis.text,
        "Recommended Next Action": hypothesis.recommended_action,
    }
    sections = [NoteSection(heading=h, body=bodies[h]) for h in _LOCKED_ORDER]

    return RenderedNote(
        title=f"PK Lead Enrichment — {lead.name}",
        sections=sections,
        enriched_at_utc=when_str,
    )
