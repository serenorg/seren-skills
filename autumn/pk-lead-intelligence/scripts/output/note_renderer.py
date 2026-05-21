"""Pure renderer for the canonical Lead Note format (Nathan template).

Section-by-section rendering of the 10-block layout Nathan paste-imports
into Salesforce (see `Customers/Autumn/Documents/PK_PasteReady_Notes_*.md`
and issue #766). The original 6-section qualification layout is
superseded — Nathan's 2026-05-19 inbound-research template is the
single source of truth.

The layout is locked: section order, section presence, and the
required audit fields (preparer date, stage, SAL) are pinned by
`tests/test_note_renderer.py`. Editing this file without updating
that test is a contract violation per SKILL.md "Privacy & Compliance".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from scripts.research.claude_angles import UltrasonicAngles
from scripts.research.linkedin_search import LinkedInCandidate
from scripts.research.perplexity import CompanyExtract, PerplexityResearch
from scripts.sf.client import LeadRow


# --------------------------------------------------------------------- #
# Output types                                                          #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class NoteSection:
    """One labeled block of the rendered Note."""

    heading: str
    body: str


@dataclass(frozen=True)
class RenderedNote:
    """Full rendered Note ready for serialization to `.docx` or SF Note.

    `enriched_at_utc` is an ISO-8601 UTC timestamp string with a
    trailing `Z`. Storing it on the envelope (not in a section) keeps
    the audit timestamp easy to read out of the SerenDB ledger without
    parsing section bodies.
    """

    title: str
    sections: list[NoteSection]
    enriched_at_utc: str


# --------------------------------------------------------------------- #
# Locked section order                                                  #
# --------------------------------------------------------------------- #


# Order is load-bearing — `tests/test_note_renderer.py` pins this list
# verbatim. Changes are a contract change; update the test in the same
# commit.
_LOCKED_HEADINGS = (
    "PK INBOUND INQUIRY RESEARCH",
    "CONTACT",
    "COMPANY",
    "TOP 3 SERVICES / PRODUCT LINES",
    "TOP ICPs",
    "MARKETS SERVED",
    "KEY PRODUCTS MADE",
    "ULTRASONIC WELDING OPPORTUNITY",
    "OWNER NOTES",
    "SOURCE",
)


# Per Nathan's template — preparer suffix, stage line, and SAL
# assignment are fixed for the PK division v1 cron. If these ever
# need to be per-Lead, plumb them via the orchestrator; do not
# hardcode-with-fallback here.
_PREPARER_SUFFIX = "(NMi)"
_STAGE_LINE = "Stage at intake: New   |   Decision: Yes   |   SAL: George Janikowski"
_ULTRASONIC_SUBTITLE = "(packaging / sealing — replace glue or heat)"
_NOT_SURFACED = "(not surfaced — confirm via direct outreach)"


# --------------------------------------------------------------------- #
# Helpers                                                               #
# --------------------------------------------------------------------- #


def _company_name(extract: Optional[CompanyExtract]) -> str:
    if extract and extract.company_name:
        return extract.company_name
    return "(company unknown)"


def _value_or_marker(value: str) -> str:
    """Return `value` if non-empty, else the not-surfaced marker."""

    return value if value.strip() else _NOT_SURFACED


def _join_or_marker(items: list[str]) -> str:
    """Numbered list from `items`, or the not-surfaced marker if empty."""

    if not items:
        return _NOT_SURFACED
    return "\n".join(f"{i + 1}) {item}" for i, item in enumerate(items[:3]))


def _build_contact_body(
    *,
    lead: LeadRow,
    extract: Optional[CompanyExtract],
    linkedin: Optional[LinkedInCandidate],
) -> str:
    """Compose the CONTACT block body in Nathan's labeled-field shape."""

    title = (extract.contact_title if extract else "") or _NOT_SURFACED
    email = (extract.contact_email if extract else "") or _NOT_SURFACED
    linkedin_url = linkedin.url if linkedin is not None else _NOT_SURFACED
    tenure_company = (
        (extract.contact_tenure_company if extract else "") or _NOT_SURFACED
    )
    tenure_role = (
        (extract.contact_tenure_role if extract else "") or _NOT_SURFACED
    )
    return (
        f"Name:     {lead.name}\n"
        f"Title:    {title}\n"
        f"Email:    {email}\n"
        f"LinkedIn: {linkedin_url}\n"
        f"Tenure at company:       {tenure_company}\n"
        f"Tenure in current role:  {tenure_role}"
    )


def _build_company_body(extract: Optional[CompanyExtract]) -> str:
    """Compose the COMPANY block in Nathan's labeled-field shape."""

    if extract is None:
        return (
            f"{_NOT_SURFACED}\n"
            f"Website: {_NOT_SURFACED}\n"
            f"Address on file: {_NOT_SURFACED}"
        )
    return (
        f"{extract.company_name or _NOT_SURFACED}\n"
        f"Website: {extract.website or _NOT_SURFACED}\n"
        f"Address on file: {extract.address or _NOT_SURFACED}"
    )


def _build_ultrasonic_body(angles: UltrasonicAngles) -> str:
    """Compose the ULTRASONIC WELDING OPPORTUNITY block.

    Subtitle is fixed per Nathan's template (`(packaging / sealing —
    replace glue or heat)`). Angles are bullet-prefixed with `•`.
    """

    if not angles.angles:
        return f"{_ULTRASONIC_SUBTITLE}\n{_NOT_SURFACED}"
    bullets = "\n".join(f"• {a}" for a in angles.angles[:3])
    return f"{_ULTRASONIC_SUBTITLE}\n{bullets}"


def _build_header_body(*, date_iso: str) -> str:
    """Header block body — preparer line + stage line."""

    return f"Prepared {date_iso} by NMi\n{_STAGE_LINE}"


def _build_source_body(lead: LeadRow) -> str:
    """SOURCE block — back-reference to the Lead's list-view URL.

    Nathan's template references the weekly spreadsheet by filename;
    we substitute the Lead's list-view URL because the skill does not
    own the operator's spreadsheet path. The contract is "where did
    this record come from?" — either pointer satisfies it.
    """

    return f"Lead list view: {lead.source_url}"


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


def render(
    *,
    lead: LeadRow,
    perplexity: PerplexityResearch,
    linkedin: Optional[LinkedInCandidate],
    angles: UltrasonicAngles,
    now: Optional[datetime] = None,
) -> RenderedNote:
    """Build the locked-layout `RenderedNote` for one enrichment.

    `now` defaults to `datetime.now(timezone.utc)`. Callers in
    production let it default; tests pin a known timestamp so the
    audit-field assertion is deterministic.
    """

    when = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when_str = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_iso = when.astimezone(timezone.utc).date().isoformat()

    extract = perplexity.extract
    company = _company_name(extract)

    title = (
        f"PK Inbound Research — {lead.name} / {company} — "
        f"{date_iso} {_PREPARER_SUFFIX}"
    )

    bodies = {
        "PK INBOUND INQUIRY RESEARCH": _build_header_body(date_iso=date_iso),
        "CONTACT": _build_contact_body(
            lead=lead, extract=extract, linkedin=linkedin
        ),
        "COMPANY": _build_company_body(extract),
        "TOP 3 SERVICES / PRODUCT LINES": _join_or_marker(
            list(extract.top_services) if extract else []
        ),
        "TOP ICPs": _value_or_marker(extract.top_icps if extract else ""),
        "MARKETS SERVED": _value_or_marker(
            extract.markets_served if extract else ""
        ),
        "KEY PRODUCTS MADE": _value_or_marker(
            extract.key_products_made if extract else ""
        ),
        "ULTRASONIC WELDING OPPORTUNITY": _build_ultrasonic_body(angles),
        "OWNER NOTES": _value_or_marker(
            extract.owner_notes if extract else ""
        ),
        "SOURCE": _build_source_body(lead),
    }

    sections = [NoteSection(heading=h, body=bodies[h]) for h in _LOCKED_HEADINGS]

    return RenderedNote(
        title=title,
        sections=sections,
        enriched_at_utc=when_str,
    )
