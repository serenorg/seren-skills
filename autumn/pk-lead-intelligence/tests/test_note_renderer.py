"""Locked-layout contract for the PK Lead enrichment Note (Nathan template).

Pins the 11-block layout the operator pastes into Salesforce — title
shape, section order, and the load-bearing audit fields (`Prepared by`,
`Stage at intake`, `SAL`). The original 6-section qualification layout
is superseded by Nathan's 2026-05-19 inbound-research template; that
template is now the single source of truth.

The test does not assert prose wording — that iterates with each
research call. The structural contract is what trips CI.
"""

from __future__ import annotations

from datetime import datetime, timezone

from scripts.output import note_renderer
from scripts.research.claude_angles import UltrasonicAngles
from scripts.research.linkedin_search import LinkedInCandidate
from scripts.research.perplexity import CompanyExtract, PerplexityResearch
from scripts.sf.client import LeadRow


_EXPECTED_HEADINGS = [
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
]


def _make_lead() -> LeadRow:
    return LeadRow(
        record_id="00QS700000PIjZnMAL",
        name="Jose Zamudio",
        source_url="https://example.lightning.force.com/lightning/o/Lead/list",
    )


def _make_perplexity() -> PerplexityResearch:
    return PerplexityResearch(
        summary="Fresh Pak processes fresh-cut produce in Detroit MI.",
        citations=["https://example.com/a"],
        raw_text="...",
        extract=CompanyExtract(
            company_name="Fresh Pak, Inc.",
            website="https://freshpakinc.com",
            address="7939 W Lafayette Blvd, Detroit MI 48209",
            top_services=[
                "Fresh-cut produce processing",
                "Private-label retail fresh-cut packaging",
                "Foodservice ingredient supply",
            ],
            top_icps="Retail grocery, foodservice operators, food manufacturers.",
            markets_served="Detroit MI HQ; U.S. Midwest retail / foodservice.",
            key_products_made="Fresh-cut fruit, vegetable, and salad packs.",
            contact_title="Operations Manager",
            contact_email="jzamudio@freshpakinc.com",
            contact_tenure_company="Not publicly available",
            contact_tenure_role="Not publicly available",
            owner_notes="Erica Perry — inbound via Website Contact-Form.",
        ),
    )


def _make_linkedin() -> LinkedInCandidate:
    return LinkedInCandidate(
        url="https://www.linkedin.com/in/jose-zamudio-abc/",
        title=None,
        match_confidence=80,
        reasons=["linkedin-profile-url", "name-tokens:jose,zamudio"],
    )


def _make_angles() -> UltrasonicAngles:
    return UltrasonicAngles(
        angles=[
            "Lidding-film sealing on fresh-cut produce clamshells — ultrasonic seals through moisture and juice.",
            "Cold-chain compatibility: no sustained heat to delicate greens at the seal line.",
            "Mono-material / recyclable lidding push: ultrasonic enables fiber-based lidstock.",
        ],
    )


def test_render_returns_eleven_block_layout() -> None:
    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=_make_perplexity(),
        linkedin=_make_linkedin(),
        angles=_make_angles(),
        now=datetime(2026, 5, 19, 10, 30, 0, tzinfo=timezone.utc),
    )

    headings = [s.heading for s in note.sections]
    assert headings == _EXPECTED_HEADINGS


def test_render_title_matches_nathan_template() -> None:
    """Title contract: `PK Inbound Research — {contact} / {company} — {date} (NMi)`.

    Contact comes from `lead.name`, company from the Perplexity extract,
    date from `now.date()`. Preparer suffix is constant `(NMi)`.
    """

    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=_make_perplexity(),
        linkedin=_make_linkedin(),
        angles=_make_angles(),
        now=datetime(2026, 5, 19, 10, 30, 0, tzinfo=timezone.utc),
    )

    assert note.title == (
        "PK Inbound Research — Jose Zamudio / Fresh Pak, Inc. — "
        "2026-05-19 (NMi)"
    )


def test_render_header_block_carries_stage_sal_and_preparer() -> None:
    """The `PK INBOUND INQUIRY RESEARCH` block holds the meta lines.

    `Prepared YYYY-MM-DD by NMi` and `Stage at intake: New | Decision:
    Yes | SAL: George Janikowski` are audit fields per Nathan's
    template — both must land in the header body verbatim so a
    downstream reader can reconstruct ownership without parsing the
    title.
    """

    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=_make_perplexity(),
        linkedin=_make_linkedin(),
        angles=_make_angles(),
        now=datetime(2026, 5, 19, 10, 30, 0, tzinfo=timezone.utc),
    )
    bodies = {s.heading: s.body for s in note.sections}

    header = bodies["PK INBOUND INQUIRY RESEARCH"]
    assert "Prepared 2026-05-19 by NMi" in header
    assert "Stage at intake: New" in header
    assert "Decision: Yes" in header
    assert "SAL: George Janikowski" in header


def test_render_ultrasonic_block_lists_three_angles() -> None:
    """The product-fit block must surface up to 3 angles, prefixed `•`."""

    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=_make_perplexity(),
        linkedin=_make_linkedin(),
        angles=_make_angles(),
        now=datetime(2026, 5, 19, 10, 30, 0, tzinfo=timezone.utc),
    )
    bodies = {s.heading: s.body for s in note.sections}

    block = bodies["ULTRASONIC WELDING OPPORTUNITY"]
    # Subtitle is fixed-form per Nathan's template.
    assert "(packaging / sealing — replace glue or heat)" in block
    # Three angle bullets land.
    assert block.count("•") == 3


def test_render_contact_block_includes_email_and_linkedin() -> None:
    """CONTACT block must surface email + LinkedIn so the SAL can reach out."""

    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=_make_perplexity(),
        linkedin=_make_linkedin(),
        angles=_make_angles(),
        now=datetime(2026, 5, 19, 10, 30, 0, tzinfo=timezone.utc),
    )
    bodies = {s.heading: s.body for s in note.sections}

    contact = bodies["CONTACT"]
    assert "Jose Zamudio" in contact
    assert "jzamudio@freshpakinc.com" in contact
    assert "https://www.linkedin.com/in/jose-zamudio-abc/" in contact


def test_render_handles_missing_extract_and_linkedin() -> None:
    """Empty extract + no LinkedIn is a valid state — layout must hold."""

    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=PerplexityResearch(
            summary="", citations=[], raw_text="", extract=None
        ),
        linkedin=None,
        angles=UltrasonicAngles(angles=[]),
        now=datetime(2026, 5, 19, 10, 30, 0, tzinfo=timezone.utc),
    )

    # Layout is locked even when content is empty — every block is
    # present so a downstream reader sees "(not surfaced)" rather than
    # a missing section.
    headings = [s.heading for s in note.sections]
    assert headings == _EXPECTED_HEADINGS

    bodies = {s.heading: s.body for s in note.sections}
    # Title falls back when extract is absent.
    assert "company unknown" in note.title.lower()
    # COMPANY block is non-empty even with no extract — it carries a
    # "(not surfaced)" marker per Nathan's "Not publicly listed" pattern.
    assert bodies["COMPANY"].strip() != ""


def test_render_default_clock_produces_utc_timestamp() -> None:
    """Default `now` -> UTC; envelope timestamp ends with `Z`."""

    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=_make_perplexity(),
        linkedin=None,
        angles=_make_angles(),
    )
    assert note.enriched_at_utc.endswith("Z")
