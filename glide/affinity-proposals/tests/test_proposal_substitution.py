from __future__ import annotations

from datetime import date

import pytest

from scripts.extract import ProposalProfile
from scripts.proposal import (
    ProposalTemplatePaths,
    extract_text_by_slide,
    write_proposal_deck,
)

pptx = pytest.importorskip("pptx")
Presentation = pptx.Presentation


def _make_template(path, variant_label: str) -> None:
    prs = Presentation()
    for idx in range(10):
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        textbox = slide.shapes.add_textbox(0, 0, 6000000, 1200000)
        frame = textbox.text_frame
        frame.text = (
            "Secured Debt Investments | Secured Debt | CLIENT_NAME | "
            "FUND_NAME | ADVISOR_NAME | DESCRIPTION | SEEKING | "
            "CURRENT_MONTH_YEAR | LAUNCH_DATE | "
            f"{variant_label}"
        )
        if idx == 9:
            frame.text = "Slide 10 disclaimer text stays unchanged."
    prs.save(path)


def test_write_proposal_deck_replaces_tokens_dates_and_preserves_slide_10(tmp_path):
    offshore = tmp_path / "offshore.pptx"
    onshore = tmp_path / "onshore.pptx"
    _make_template(offshore, "OFFSHORE_VARIANT")
    _make_template(onshore, "ONSHORE_VARIANT")

    profile = ProposalProfile(
        client_name="Acme Capital",
        description="Acme is preparing an institutional launch.",
        seeking=["feeder funds", "launch planning"],
        structure="onshore",
        fund_name="Acme Credit Fund",
        advisor_name="Acme Advisors",
    )
    out = tmp_path / "out.pptx"

    artifact = write_proposal_deck(
        profile,
        templates=ProposalTemplatePaths(offshore=offshore, onshore=onshore),
        output_path=out,
        today=date(2026, 6, 4),
    )

    assert artifact.template_used == onshore
    slides = extract_text_by_slide(out)
    all_text = "\n".join(slides)
    assert "Secured Debt" not in all_text
    assert "CLIENT_NAME" not in all_text
    assert "Acme Capital" in all_text
    assert "July 4, 2026" in all_text
    assert "June 2026" in all_text
    assert "ONSHORE_VARIANT" in all_text
    assert slides[9] == "Slide 10 disclaimer text stays unchanged."
