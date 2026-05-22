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


# --------------------------------------------------------------------- #
# Issue #781 — LinkedIn profile integration                              #
# --------------------------------------------------------------------- #


def _make_profile(*, current_company: str = "Fresh Pak, Inc."):
    """Stub a populated LinkedInProfile for renderer tests.

    Local import keeps the existing tests independent of the scraper
    module — if the scraper file is renamed, the older tests keep
    running.
    """

    from scripts.research.linkedin_scraper import (
        ActivityItem,
        Education,
        LinkedInProfile,
        PriorRole,
    )

    return LinkedInProfile(
        url="https://www.linkedin.com/in/jose-zamudio-abc/",
        headline="Operations Manager at Fresh Pak",
        current_title="Operations Manager",
        current_company=current_company,
        current_tenure_months=28,
        location="Detroit, MI",
        prior_roles=[
            PriorRole(
                title="Operations Manager",
                company=current_company,
                duration_label="Jan 2023 - Present · 2 yrs 4 mos",
            ),
            PriorRole(
                title="Production Supervisor",
                company="MidwestPack Co",
                duration_label="2019 - 2022 · 3 yrs",
            ),
        ],
        education=[
            Education(
                school="Wayne State University",
                degree="BS",
                field="Industrial Engineering",
                duration_label="2014 - 2018",
            ),
        ],
        skills=["Operations", "Lean Manufacturing", "Packaging"],
        recent_activity=[
            ActivityItem(
                kind="post",
                snippet="Cut glue from our top-seal line — ultrasonic pilot showed "
                "20% throughput lift on lidstock changes.",
                posted_at_label="3d",
            ),
        ],
        fetched_at_utc="2026-05-22T07:00:00Z",
    )


def test_render_with_profile_populates_contact_tenure() -> None:
    """A populated profile must surface tenure on the CONTACT block.

    Without the profile, `Tenure at company` falls back to whatever
    Perplexity returned (often "Not publicly available"). With the
    profile, the scraped months value lands on the Note.
    """

    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=_make_perplexity(),
        linkedin=_make_linkedin(),
        angles=_make_angles(),
        profile=_make_profile(),
        now=datetime(2026, 5, 19, 10, 30, 0, tzinfo=timezone.utc),
    )
    bodies = {s.heading: s.body for s in note.sections}

    contact = bodies["CONTACT"]
    # Tenure shows up in human-readable form (months → "2y 4mo").
    assert "2y 4mo" in contact
    # The profile-derived current title also lands.
    assert "Operations Manager" in contact


def test_render_with_profile_company_mismatch_lands_in_owner_notes() -> None:
    """When LinkedIn-derived company differs from Salesforce / Perplexity,
    the renderer must surface the mismatch as an OWNER NOTES bullet —
    never silently overwrite the Salesforce-side company value.

    The COMPANY block keeps the Perplexity-extracted name as
    authoritative; the OWNER NOTES block carries the audit signal so
    the reviewer sees the conflict.
    """

    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=_make_perplexity(),  # company = "Fresh Pak, Inc."
        linkedin=_make_linkedin(),
        angles=_make_angles(),
        profile=_make_profile(current_company="Old Acquired Co"),
        now=datetime(2026, 5, 19, 10, 30, 0, tzinfo=timezone.utc),
    )
    bodies = {s.heading: s.body for s in note.sections}

    # COMPANY block stays Salesforce-authoritative.
    assert "Fresh Pak" in bodies["COMPANY"]
    # OWNER NOTES surfaces the discrepancy.
    assert "Old Acquired Co" in bodies["OWNER NOTES"]
    assert "LinkedIn" in bodies["OWNER NOTES"]


def test_render_with_profile_appends_audit_to_source_block() -> None:
    """SOURCE block must record that the LinkedIn scraper ran so a
    reviewer can audit which Notes used profile data.
    """

    note = note_renderer.render(
        lead=_make_lead(),
        perplexity=_make_perplexity(),
        linkedin=_make_linkedin(),
        angles=_make_angles(),
        profile=_make_profile(),
        now=datetime(2026, 5, 19, 10, 30, 0, tzinfo=timezone.utc),
    )
    bodies = {s.heading: s.body for s in note.sections}

    source = bodies["SOURCE"]
    assert "LinkedIn (scraped" in source
    assert "2026-05-22T07:00:00Z" in source


def test_render_profile_none_path_unchanged() -> None:
    """Regression guard: `profile=None` (the default) must produce the
    exact same Note as not passing the kwarg at all.

    Existing rendering tests do not pass `profile`. Adding the new
    optional kwarg must not change their output by a single byte; this
    test pins the equivalence explicitly so a refactor catches it.
    """

    common = dict(
        lead=_make_lead(),
        perplexity=_make_perplexity(),
        linkedin=_make_linkedin(),
        angles=_make_angles(),
        now=datetime(2026, 5, 19, 10, 30, 0, tzinfo=timezone.utc),
    )
    without_kwarg = note_renderer.render(**common)
    with_none_kwarg = note_renderer.render(profile=None, **common)

    assert without_kwarg.title == with_none_kwarg.title
    assert [s.body for s in without_kwarg.sections] == [
        s.body for s in with_none_kwarg.sections
    ]
