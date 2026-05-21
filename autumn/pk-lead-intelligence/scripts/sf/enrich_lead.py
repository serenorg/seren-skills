"""Per-lead enrichment orchestrator.

Pure orchestrator over injectable adapters. Takes one `LeadRow`, fans
research out across Perplexity + LinkedIn + Claude, renders the
locked Note, writes a `.docx` to disk, and returns a structured
`EnrichmentResult`.

Every external call enters through the `Dependencies` bundle so tests
can wire stubs without network, Playwright, or python-docx. The
production wiring lives in `scripts/agent.py`.

This module also owns `is_packaging_lead` — the cross-division
mis-routing defense. The function is here NOW (Phase 2) even though
the upstream data does not yet carry a PACKAGING column, so a Phase 4
write path cannot accidentally ship without the gate. Phase 3 will
extend `LeadRow` with `is_packaging` from the All Sources PK Leads
report and this function will immediately start enforcing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from scripts.output import dryrun_docx
from scripts.output.note_renderer import RenderedNote, render
from scripts.research.claude_angles import UltrasonicAngles
from scripts.research.linkedin_search import LinkedInCandidate
from scripts.research.perplexity import PerplexityResearch
from scripts.sf.client import LeadRow


# --------------------------------------------------------------------- #
# Dependency injection                                                  #
# --------------------------------------------------------------------- #


PerplexityFn = Callable[..., PerplexityResearch]
LinkedInFn = Callable[..., list[LinkedInCandidate]]
AnglesFn = Callable[..., UltrasonicAngles]
DocxWriterFn = Callable[..., Path]
ClockFn = Callable[[], datetime]


@dataclass(frozen=True)
class Dependencies:
    """Injectable adapter bundle.

    Tests pass stubs; `scripts/agent.py` passes the live adapters from
    `scripts.research.*` and `scripts.output.dryrun_docx`.
    """

    perplexity_research: PerplexityFn
    linkedin_discover: LinkedInFn
    claude_angles: AnglesFn
    docx_writer: DocxWriterFn = dryrun_docx.write
    clock: Optional[ClockFn] = None


# --------------------------------------------------------------------- #
# Result type                                                           #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class EnrichmentResult:
    """Bundle returned from a single `enrich` call.

    Carries the rendered Note, the path of the written `.docx`, and
    the upstream research artifacts. The research artifacts are kept
    so a caller (e.g. the SerenDB persistence layer in Phase 4) can
    log them alongside the Note without re-charging SerenBucks.
    """

    note: RenderedNote
    docx_path: Path
    perplexity: PerplexityResearch
    linkedin: Optional[LinkedInCandidate]
    angles: UltrasonicAngles


# --------------------------------------------------------------------- #
# Division-boundary gate                                                #
# --------------------------------------------------------------------- #


def is_packaging_lead(lead: object) -> bool:
    """Return True iff `lead` is in the PK division.

    Reads the `is_packaging` attribute via `getattr` with a False
    default so any `LeadRow`-shaped object without the field fails
    closed. The current `LeadRow` does not carry the field; Phase 3
    extends it from the All Sources PK Leads report column and this
    function immediately starts admitting PK rows.

    Keeping the gate alive in Phase 2 (when it always returns False)
    is a defense-in-depth measure: a Phase 4 write path can be wired
    against `is_packaging_lead` today without risk of a mis-routed
    Note ever landing on a non-PK Lead.
    """

    value = getattr(lead, "is_packaging", False)
    return bool(value)


# --------------------------------------------------------------------- #
# Orchestrator                                                          #
# --------------------------------------------------------------------- #


def _safe_filename_fragment(name: str, *, max_len: int = 64) -> str:
    """Convert a free-form Lead name into a filesystem-safe fragment.

    Replaces every non-alphanumeric character with `_` and truncates.
    The Salesforce record id stays the canonical handle on the path;
    this fragment is only there so a directory of dry-run outputs is
    greppable by Lead name.
    """

    cleaned = "".join(c if c.isalnum() else "_" for c in name)
    return cleaned[:max_len] or "lead"


def enrich(
    *,
    lead: LeadRow,
    deps: Dependencies,
    company_hint: Optional[str],
    output_dir: Path,
) -> EnrichmentResult:
    """Run the full research → render → docx pipeline for one Lead.

    No retries, no fallbacks — any adapter that raises surfaces the
    exception unchanged. Phase 2 callers (the dry-run path) want the
    failure to be loud; Phase 4+ callers can wrap this if they need
    retry behavior.
    """

    perplexity = deps.perplexity_research(
        lead_name=lead.name,
        source_hint=lead.source_url,
    )

    candidates = deps.linkedin_discover(
        lead_name=lead.name,
        company_hint=company_hint,
    )
    linkedin = candidates[0] if candidates else None

    # Prefer the company name Perplexity extracted from the live page;
    # fall back to `company_hint` (passed by the caller, currently None
    # until #794 plumbs it from the All Sources PK Leads report). This
    # is what reaches the Claude angle prompt — it lets the model name
    # the customer directly, which materially improves angle quality.
    extracted_company = (
        perplexity.extract.company_name
        if perplexity.extract and perplexity.extract.company_name
        else (company_hint or "")
    )

    angles = deps.claude_angles(
        lead_name=lead.name,
        company_name=extracted_company,
        perplexity_summary=perplexity.summary,
    )

    note = render(
        lead=lead,
        perplexity=perplexity,
        linkedin=linkedin,
        angles=angles,
        now=deps.clock() if deps.clock else None,
    )

    filename = f"{lead.record_id}_{_safe_filename_fragment(lead.name)}.docx"
    docx_path = output_dir / filename
    written = deps.docx_writer(note=note, output_path=docx_path)

    return EnrichmentResult(
        note=note,
        docx_path=written,
        perplexity=perplexity,
        linkedin=linkedin,
        angles=angles,
    )
