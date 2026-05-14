"""Dry-run .docx output contract.

The dry-run path is the only way an operator validates the Note
format before flipping `live_mode=true`. If `dryrun_docx.write` ever
ships a corrupt or empty file, the 5-Note operator review loop in
SKILL.md "Pre-Run Checklist" becomes a no-op.

This test writes a Note to a tempfile and reads it back via
`docx.Document` to assert (a) the file parses as a real .docx and
(b) every locked-layout section heading + body lands on the page.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

docx = pytest.importorskip("docx")  # python-docx; installed via requirements.txt

from scripts.output import dryrun_docx, note_renderer
from scripts.research.claude_hypothesis import Hypothesis
from scripts.research.linkedin_search import LinkedInCandidate
from scripts.research.perplexity import PerplexityResearch
from scripts.sf.client import LeadRow


def _build_note() -> note_renderer.RenderedNote:
    return note_renderer.render(
        lead=LeadRow(
            record_id="00Q5g00000ABCDEFGH",
            name="Jane Operator",
            source_url="https://example.lightning.force.com/lightning/o/Lead/list",
        ),
        perplexity=PerplexityResearch(
            summary="Acme Packaging Q1 expansion.",
            citations=["https://example.com/a"],
            raw_text="...",
        ),
        linkedin=LinkedInCandidate(
            url="https://www.linkedin.com/in/jane-operator-abc/",
            title=None,
            match_confidence=80,
            reasons=["linkedin-profile-url"],
        ),
        hypothesis=Hypothesis(
            text="Active capacity expansion implies PK pipeline budget.",
            recommended_action="Send the PK packaging deck.",
        ),
        now=datetime(2026, 5, 14, 10, 30, 0, tzinfo=timezone.utc),
    )


def test_writes_parseable_docx_with_locked_sections(tmp_path: Path) -> None:
    note = _build_note()
    output_path = tmp_path / "out" / "lead_jane.docx"

    written = dryrun_docx.write(note=note, output_path=output_path)

    assert written == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0

    # Round-trip: read the docx back and confirm the locked layout
    # actually landed on the page. python-docx exposes every paragraph
    # (including headings) via `.paragraphs`.
    doc = docx.Document(str(output_path))
    text = "\n".join(p.text for p in doc.paragraphs)

    for heading in [
        "Lead",
        "Sources Used",
        "Research Summary",
        "LinkedIn",
        "Hypothesis",
        "Recommended Next Action",
    ]:
        assert heading in text, f"Locked section {heading!r} missing from docx"

    # Audit fields must be present in the rendered file too — not just
    # in the in-memory `RenderedNote`.
    assert "00Q5g00000ABCDEFGH" in text
    assert "2026-05-14T10:30:00Z" in text


def test_writes_creates_parent_directory(tmp_path: Path) -> None:
    """Caller hands a path whose parent does not exist; writer creates it."""

    note = _build_note()
    nested = tmp_path / "deeply" / "nested" / "out.docx"

    dryrun_docx.write(note=note, output_path=nested)

    assert nested.exists()
