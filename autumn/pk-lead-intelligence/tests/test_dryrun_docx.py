"""Dry-run .docx output contract — Nathan's 11-block template.

The dry-run path is the only artifact the operator inspects during
the 5-Note review loop in SKILL.md "Pre-Run Checklist". If
`dryrun_docx.write` ever ships a corrupt or empty file, the review
loop becomes a no-op.

This test writes a Note to a tempfile and reads it back via
`docx.Document` to assert (a) the file parses as a real .docx and
(b) every Nathan-template section heading + body lands on the page.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

docx = pytest.importorskip("docx")  # python-docx; installed via requirements.txt

from scripts.output import dryrun_docx, note_renderer
from scripts.research.claude_angles import UltrasonicAngles
from scripts.research.linkedin_search import LinkedInCandidate
from scripts.research.perplexity import CompanyExtract, PerplexityResearch
from scripts.sf.client import LeadRow


def _build_note() -> note_renderer.RenderedNote:
    return note_renderer.render(
        lead=LeadRow(
            record_id="00QS700000PIjZnMAL",
            name="Jose Zamudio",
            source_url="https://example.lightning.force.com/lightning/o/Lead/list",
        ),
        perplexity=PerplexityResearch(
            summary="Fresh Pak fresh-cut produce.",
            citations=["https://example.com/a"],
            raw_text="...",
            extract=CompanyExtract(
                company_name="Fresh Pak, Inc.",
                website="https://freshpakinc.com",
                address="7939 W Lafayette Blvd, Detroit MI 48209",
                top_services=["Fresh-cut produce"],
                top_icps="Retail grocery",
                markets_served="U.S. Midwest",
                key_products_made="Fresh-cut salad packs",
                contact_title="Operations Manager",
                contact_email="jzamudio@freshpakinc.com",
                contact_tenure_company="N/A",
                contact_tenure_role="N/A",
                owner_notes="Erica Perry — Website Contact-Form.",
            ),
        ),
        linkedin=LinkedInCandidate(
            url="https://www.linkedin.com/in/jose-zamudio-abc/",
            title=None,
            match_confidence=80,
            reasons=["linkedin-profile-url"],
        ),
        angles=UltrasonicAngles(
            angles=[
                "Lidding-film sealing on fresh-cut clamshells.",
                "Cold-chain compatibility.",
                "Mono-material recyclable lidding.",
            ],
        ),
        now=datetime(2026, 5, 19, 10, 30, 0, tzinfo=timezone.utc),
    )


def test_writes_parseable_docx_with_eleven_block_layout(tmp_path: Path) -> None:
    note = _build_note()
    output_path = tmp_path / "out" / "lead_jose.docx"

    written = dryrun_docx.write(note=note, output_path=output_path)

    assert written == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0

    # Round-trip: read the docx back and confirm every Nathan-template
    # section actually landed on the page.
    doc = docx.Document(str(output_path))
    text = "\n".join(p.text for p in doc.paragraphs)

    for heading in [
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
    ]:
        assert heading in text, f"Template section {heading!r} missing from docx"

    # Audit fields must be present in the rendered file, not just on
    # the in-memory `RenderedNote`.
    assert "Jose Zamudio" in text
    assert "Fresh Pak, Inc." in text
    assert "SAL: George Janikowski" in text
    assert "2026-05-19" in text


def test_writes_creates_parent_directory(tmp_path: Path) -> None:
    """Caller hands a path whose parent does not exist; writer creates it."""

    note = _build_note()
    nested = tmp_path / "deeply" / "nested" / "out.docx"

    dryrun_docx.write(note=note, output_path=nested)

    assert nested.exists()
