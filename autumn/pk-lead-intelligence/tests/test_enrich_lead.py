"""Orchestrator + division-boundary contract for enrich_lead.

Two concerns, both load-bearing:

1. The orchestrator wires research → render → docx through the
   injectable Dependencies bundle. If a stub never reaches an
   adapter, the pipeline silently drops a step — and a downstream
   reviewer sees an empty section instead of an error.

2. The `is_packaging_lead` gate is the cross-division mis-routing
   defense called out in issue #530's test plan. A Phase 4 write
   path that bypasses this gate would land a PK Note on a PL / MD /
   NW Lead, which SKILL.md flags as a P0 defect.

Updated for issue #766: the Hypothesis adapter is now an
UltrasonicAngles generator (HU-specific selling-thesis input). The
orchestrator contract is otherwise unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from scripts.research.claude_angles import UltrasonicAngles
from scripts.research.linkedin_search import LinkedInCandidate
from scripts.research.perplexity import CompanyExtract, PerplexityResearch
from scripts.sf import enrich_lead
from scripts.sf.client import LeadRow


# --------------------------------------------------------------------- #
# Stub adapters                                                         #
# --------------------------------------------------------------------- #


@dataclass
class _Calls:
    perplexity: list[dict] = field(default_factory=list)
    linkedin: list[dict] = field(default_factory=list)
    angles: list[dict] = field(default_factory=list)
    docx: list[dict] = field(default_factory=list)


def _build_stub_deps(
    *,
    linkedin_candidates: list[LinkedInCandidate] | None = None,
    calls: _Calls | None = None,
) -> tuple[enrich_lead.Dependencies, _Calls]:
    calls = calls or _Calls()
    candidates = linkedin_candidates if linkedin_candidates is not None else [
        LinkedInCandidate(
            url="https://www.linkedin.com/in/jane-operator/",
            title=None,
            match_confidence=80,
            reasons=["name-tokens:jane,operator"],
        )
    ]

    def fake_perplexity(*, lead_name: str, source_hint: str) -> PerplexityResearch:
        calls.perplexity.append({"lead_name": lead_name, "source_hint": source_hint})
        return PerplexityResearch(
            summary="Acme expansion.",
            citations=["https://example.com/a"],
            raw_text="...",
            extract=CompanyExtract(
                company_name="Acme Packaging",
                website="https://acme.example",
                address="123 Acme Way",
                top_services=["Service A"],
                top_icps="Retail",
                markets_served="U.S.",
                key_products_made="Pouches",
                contact_title="Buyer",
                contact_email="jane@acme.example",
                contact_tenure_company="3 years",
                contact_tenure_role="1 year",
                owner_notes="Inbound via web form.",
            ),
        )

    def fake_linkedin(*, lead_name: str, company_hint: Optional[str]) -> list[LinkedInCandidate]:
        calls.linkedin.append({"lead_name": lead_name, "company_hint": company_hint})
        return candidates

    def fake_angles(
        *,
        lead_name: str,
        company_name: str,
        perplexity_summary: str,
    ) -> UltrasonicAngles:
        calls.angles.append(
            {
                "lead_name": lead_name,
                "company_name": company_name,
                "perplexity_summary": perplexity_summary,
            }
        )
        return UltrasonicAngles(
            angles=["Lidding-film angle.", "Cold-chain angle.", "Mono-material angle."],
        )

    def fake_docx_writer(*, note, output_path: Path) -> Path:
        calls.docx.append({"output_path": output_path, "title": note.title})
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"PK")  # sentinel; real writer is tested elsewhere
        return output_path

    deps = enrich_lead.Dependencies(
        perplexity_research=fake_perplexity,
        linkedin_discover=fake_linkedin,
        claude_angles=fake_angles,
        docx_writer=fake_docx_writer,
        clock=lambda: datetime(2026, 5, 19, 10, 30, 0, tzinfo=timezone.utc),
    )
    return deps, calls


# --------------------------------------------------------------------- #
# Orchestrator wiring                                                   #
# --------------------------------------------------------------------- #


def _make_lead() -> LeadRow:
    return LeadRow(
        record_id="00QS700000PIjZnMAL",
        name="Jane Operator",
        source_url="https://example.lightning.force.com/lightning/o/Lead/list",
    )


def test_enrich_invokes_every_adapter_in_order(tmp_path: Path) -> None:
    deps, calls = _build_stub_deps()
    lead = _make_lead()

    result = enrich_lead.enrich(
        lead=lead,
        deps=deps,
        company_hint="Acme Packaging",
        output_dir=tmp_path,
    )

    # Every adapter saw exactly one call.
    assert len(calls.perplexity) == 1
    assert len(calls.linkedin) == 1
    assert len(calls.angles) == 1
    assert len(calls.docx) == 1

    # The angle generator receives the extracted company name so its
    # prompt can name the customer directly — a load-bearing thread
    # for issue #766 (Nathan's template expects company-specific angles).
    assert calls.angles[0]["company_name"] == "Acme Packaging"

    # docx path lives inside the requested output_dir and includes the
    # record id so a Lead's audit trail is greppable from disk.
    assert result.docx_path.parent == tmp_path
    assert "00QS700000PIjZnMAL" in result.docx_path.name
    assert result.docx_path.exists()


def test_enrich_pipes_perplexity_company_to_linkedin_discover(tmp_path: Path) -> None:
    """Perplexity-extracted company name must reach linkedin_discover.

    Before this wiring, linkedin_discover saw `company_hint=None`
    whenever the caller didn't pass an explicit hint — even when
    Perplexity had already identified the company. That dropped the
    company-token boost in scoring and left scored candidates capped
    at ~70 instead of 100. Setup keeps the two paths distinguishable:
    caller hint is None, Perplexity extract carries "Acme Packaging".
    """

    deps, calls = _build_stub_deps()
    result = enrich_lead.enrich(
        lead=_make_lead(),
        deps=deps,
        company_hint=None,
        output_dir=tmp_path,
    )

    assert calls.linkedin[0]["company_hint"] == "Acme Packaging"
    # Sibling invariant: same name flows to angle generator.
    assert calls.angles[0]["company_name"] == "Acme Packaging"
    assert result.docx_path.exists()


def test_enrich_handles_empty_linkedin_candidates(tmp_path: Path) -> None:
    """No candidates is a valid state; pipeline must still render + write."""

    deps, calls = _build_stub_deps(linkedin_candidates=[])
    result = enrich_lead.enrich(
        lead=_make_lead(),
        deps=deps,
        company_hint=None,
        output_dir=tmp_path,
    )

    assert result.linkedin is None
    # Angles still gets called — selling thesis runs even without
    # a LinkedIn match.
    assert len(calls.angles) == 1
    assert result.docx_path.exists()


# --------------------------------------------------------------------- #
# Division-boundary gate                                                #
# --------------------------------------------------------------------- #


def test_is_packaging_lead_false_when_field_missing() -> None:
    """LeadRow without `is_packaging` -> gate fails closed."""

    lead = LeadRow(record_id="00Q", name="X", source_url="")
    assert enrich_lead.is_packaging_lead(lead) is False


def test_is_packaging_lead_reads_attribute_when_present() -> None:
    """A LeadRow-shaped object with `is_packaging=True` reads True."""

    @dataclass(frozen=True)
    class _LeadWithDivision:
        record_id: str
        name: str
        source_url: str
        is_packaging: bool

    pk_lead = _LeadWithDivision(
        record_id="00Q", name="X", source_url="", is_packaging=True
    )
    non_pk_lead = _LeadWithDivision(
        record_id="00Q", name="Y", source_url="", is_packaging=False
    )

    assert enrich_lead.is_packaging_lead(pk_lead) is True
    assert enrich_lead.is_packaging_lead(non_pk_lead) is False


# --------------------------------------------------------------------- #
# Issue #781 — LinkedIn scraper wiring                                  #
# --------------------------------------------------------------------- #


def test_enrich_with_linkedin_scrape_none_keeps_existing_behavior(
    tmp_path: Path,
) -> None:
    """Default `Dependencies.linkedin_scrape=None` must not change a
    single byte of the produced EnrichmentResult vs the pre-#781
    state. The flag-off path is the dominant production path; a
    regression here would land in every Note.
    """

    deps, _calls = _build_stub_deps()
    result = enrich_lead.enrich(
        lead=_make_lead(),
        deps=deps,
        company_hint=None,
        output_dir=tmp_path,
    )

    # The new field defaults to None when no scraper is wired.
    assert result.profile is None
    # The existing surface is intact.
    assert result.linkedin is not None
    assert result.docx_path.exists()


def test_enrich_with_linkedin_scrape_wired_plumbs_profile(tmp_path: Path) -> None:
    """When a scraper is wired, the top-confidence candidate URL is
    passed in, the returned profile rides on EnrichmentResult, and the
    profile reaches the rendered Note.
    """

    from scripts.research.linkedin_scraper import LinkedInProfile

    scrape_calls: list[str] = []

    def fake_scrape(*, profile_url: str) -> LinkedInProfile:
        scrape_calls.append(profile_url)
        return LinkedInProfile(
            url=profile_url,
            headline="Operator headline",
            current_title="Director",
            current_company="Acme Packaging",
            current_tenure_months=14,
            location="Toronto, ON",
            prior_roles=[],
            education=[],
            skills=[],
            recent_activity=[],
            fetched_at_utc="2026-05-22T07:00:00Z",
        )

    deps, _ = _build_stub_deps()
    deps_with_scrape = enrich_lead.Dependencies(
        perplexity_research=deps.perplexity_research,
        linkedin_discover=deps.linkedin_discover,
        claude_angles=deps.claude_angles,
        docx_writer=deps.docx_writer,
        clock=deps.clock,
        linkedin_scrape=fake_scrape,
    )

    result = enrich_lead.enrich(
        lead=_make_lead(),
        deps=deps_with_scrape,
        company_hint=None,
        output_dir=tmp_path,
    )

    # Top-confidence candidate URL was passed to the scraper.
    assert scrape_calls == ["https://www.linkedin.com/in/jane-operator/"]
    # The profile reached the result.
    assert result.profile is not None
    assert result.profile.current_company == "Acme Packaging"
    # And the rendered Note saw the scraped tenure value.
    contact_body = next(
        s.body for s in result.note.sections if s.heading == "CONTACT"
    )
    assert "1y 2mo" in contact_body


def test_enrich_scrape_below_min_confidence_does_not_call_scraper(
    tmp_path: Path,
) -> None:
    """`min_confidence` gates the scraper so low-confidence matches do
    not waste a navigation. Default is `70`; a 50-confidence candidate
    must skip the scrape entirely.
    """

    scrape_calls: list[str] = []

    def fake_scrape(*, profile_url: str):
        scrape_calls.append(profile_url)
        return None

    deps, _ = _build_stub_deps(
        linkedin_candidates=[
            LinkedInCandidate(
                url="https://www.linkedin.com/in/low-conf/",
                title=None,
                match_confidence=50,  # below default 70
                reasons=["url-only"],
            )
        ]
    )
    deps_with_scrape = enrich_lead.Dependencies(
        perplexity_research=deps.perplexity_research,
        linkedin_discover=deps.linkedin_discover,
        claude_angles=deps.claude_angles,
        docx_writer=deps.docx_writer,
        clock=deps.clock,
        linkedin_scrape=fake_scrape,
        linkedin_scrape_min_confidence=70,
    )

    result = enrich_lead.enrich(
        lead=_make_lead(),
        deps=deps_with_scrape,
        company_hint=None,
        output_dir=tmp_path,
    )

    assert scrape_calls == []  # below threshold — no navigation
    assert result.profile is None
    # And `linkedin_attempted` is False on the result so the summary
    # line does not count this as a signed-out failure.
    assert result.linkedin_scrape_attempted is False
