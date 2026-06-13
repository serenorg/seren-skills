"""Render the shipped templates and prove every issue-#980 gap is closed.

Critical regression test: drives `write_proposal_deck` against the real
`glide_proposal_{offshore,onshore}.pptx` files committed under
assets/templates/. The prior synthetic fixture masked the token/template
mismatch that put example-client residue (March 2026, Coral Gables, ROAM,
Secured Debt, April 15) into a customer-facing deck.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from scripts.extract import ProposalProfile
from scripts.proposal import (
    ProposalTemplatePaths,
    extract_text_by_slide,
    write_proposal_deck,
)

pptx = pytest.importorskip("pptx")
Presentation = pptx.Presentation

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "assets" / "templates"
OFFSHORE = TEMPLATES_DIR / "glide_proposal_offshore.pptx"
ONSHORE = TEMPLATES_DIR / "glide_proposal_onshore.pptx"

CLIENT_NAME = "Acme Real Estate Partners"
DESCRIPTION = "Acme is a Chicago-based real estate operator."
# 2026-06-05 → CURRENT_MONTH_YEAR = "June 2026"; LAUNCH_DATE = "July 5, 2026".
TODAY = date(2026, 6, 5)

STALE_LITERALS = (
    "Secured Debt",
    "March 2026",
    "April 15, 2026",
    "Coral Gables",
    "ROAM",
    "Latin American",
    "Concurrent",
    "CLIENT_NAME",
    "CURRENT_MONTH_YEAR",
    "LAUNCH_DATE",
    "DESCRIPTION",
)


@pytest.mark.parametrize(
    "structure,template",
    [("offshore", OFFSHORE), ("onshore", ONSHORE)],
)
def test_shipped_templates_render_with_no_residue_and_correct_customizations(
    structure: str, template: Path, tmp_path: Path
) -> None:
    profile = ProposalProfile(
        client_name=CLIENT_NAME,
        description=DESCRIPTION,
        seeking=["feeder funds", "operations support"],
        structure=structure,
        fund_name=f"{CLIENT_NAME} Fund",
        advisor_name=CLIENT_NAME,
    )
    out = tmp_path / f"out_{structure}.pptx"

    artifact = write_proposal_deck(
        profile,
        templates=ProposalTemplatePaths(offshore=OFFSHORE, onshore=ONSHORE),
        output_path=out,
        today=TODAY,
    )
    assert artifact.template_used == template
    assert artifact.file_name.endswith(".pptx")

    slides = extract_text_by_slide(out)
    all_text = "\n".join(slides)

    for stale in STALE_LITERALS:
        assert stale not in all_text, f"stale {stale!r} survived in rendered deck"

    assert CLIENT_NAME in slides[0]      # cover
    assert "June 2026" in slides[0]      # cover date
    assert DESCRIPTION in slides[4]      # slide-5 description
    assert CLIENT_NAME in slides[5]      # slide-6 GROUP-shape diagram boxes
    assert "July 5, 2026" in slides[7]   # slide-8 launch date

    prs = Presentation(str(out))
    assert prs.core_properties.title == f"Glide - {CLIENT_NAME} Proposal"
