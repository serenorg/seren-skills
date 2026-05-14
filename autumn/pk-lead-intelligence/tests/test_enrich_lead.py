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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from scripts.research.claude_hypothesis import Hypothesis
from scripts.research.linkedin_search import LinkedInCandidate
from scripts.research.perplexity import PerplexityResearch
from scripts.sf import enrich_lead
from scripts.sf.client import LeadRow


# --------------------------------------------------------------------- #
# Stub adapters                                                         #
# --------------------------------------------------------------------- #


@dataclass
class _Calls:
    perplexity: list[dict] = field(default_factory=list)
    linkedin: list[dict] = field(default_factory=list)
    hypothesis: list[dict] = field(default_factory=list)
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
        )

    def fake_linkedin(*, lead_name: str, company_hint: Optional[str]) -> list[LinkedInCandidate]:
        calls.linkedin.append({"lead_name": lead_name, "company_hint": company_hint})
        return candidates

    def fake_hypothesis(
        *, lead_name: str, perplexity_summary: str, linkedin_url: Optional[str]
    ) -> Hypothesis:
        calls.hypothesis.append(
            {
                "lead_name": lead_name,
                "perplexity_summary": perplexity_summary,
                "linkedin_url": linkedin_url,
            }
        )
        return Hypothesis(
            text="Hypothesis body.",
            recommended_action="Send deck.",
        )

    def fake_docx_writer(*, note, output_path: Path) -> Path:
        calls.docx.append({"output_path": output_path, "title": note.title})
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"PK")  # sentinel; real writer is tested elsewhere
        return output_path

    deps = enrich_lead.Dependencies(
        perplexity_research=fake_perplexity,
        linkedin_discover=fake_linkedin,
        claude_hypothesis=fake_hypothesis,
        docx_writer=fake_docx_writer,
        clock=lambda: datetime(2026, 5, 14, 10, 30, 0, tzinfo=timezone.utc),
    )
    return deps, calls


# --------------------------------------------------------------------- #
# Orchestrator wiring                                                   #
# --------------------------------------------------------------------- #


def _make_lead() -> LeadRow:
    return LeadRow(
        record_id="00Q5g00000ABCDEFGH",
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
    assert len(calls.hypothesis) == 1
    assert len(calls.docx) == 1

    # Adapters received the right inputs.
    assert calls.perplexity[0]["lead_name"] == lead.name
    assert calls.linkedin[0]["company_hint"] == "Acme Packaging"
    assert calls.hypothesis[0]["linkedin_url"] == "https://www.linkedin.com/in/jane-operator/"

    # docx path lives inside the requested output_dir and includes the
    # record id so a Lead's audit trail is greppable from disk.
    assert result.docx_path.parent == tmp_path
    assert "00Q5g00000ABCDEFGH" in result.docx_path.name
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
    # Claude still gets called — hypothesis runs even without a match.
    assert calls.hypothesis[0]["linkedin_url"] is None
    assert result.docx_path.exists()


# --------------------------------------------------------------------- #
# Division-boundary gate                                                #
# --------------------------------------------------------------------- #


def test_is_packaging_lead_false_when_field_missing() -> None:
    """LeadRow has no PACKAGING field yet (Phase 3) — gate must fail closed.

    This is the load-bearing assertion: until Phase 3 wires the
    All Sources PK Leads report and extends LeadRow with the column,
    `is_packaging_lead` must return False so the future Phase 4 write
    path cannot fire.
    """

    lead = LeadRow(
        record_id="00Q",
        name="X",
        source_url="",
    )
    assert enrich_lead.is_packaging_lead(lead) is False


def test_is_packaging_lead_reads_attribute_when_present() -> None:
    """A LeadRow-shaped object with `is_packaging=True` reads True.

    Validates the Phase 3 hook: once `fetch_first_lead` (or the report
    reader) attaches `is_packaging`, this gate immediately starts
    enforcing the PK boundary without further code changes.
    """

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
