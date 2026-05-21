"""Critical tests for scripts/output/weekly_status.py.

The weekly doc is a pure render of the past 7 days of enrichments
plus pipeline pacing context. Two shape contracts matter:

* Zero-enrichment week: doc still renders cleanly so the operator
  knows nothing happened (vs. thinking the run failed).
* Some-enrichment week: lead count + per-lead lines render in
  the locked layout the Phase 5 slash command will parse.
"""

from __future__ import annotations

from scripts.output import weekly_status


# --------------------------------------------------------------------- #
# Shape contract                                                        #
# --------------------------------------------------------------------- #


def test_weekly_doc_renders_with_zero_enrichments():
    """Empty-week doc is not an error. The operator-facing line
    "no enrichments this week" must appear so a successful empty
    run is visibly distinct from a failure."""

    doc = weekly_status.compose_weekly_status_doc(
        week_label="2026-W19",
        lead_summaries=[],
        monthly_close_target_usd=500_000,
    )

    assert doc.title == "PK Weekly Status — 2026-W19"
    assert doc.lead_count == 0
    assert "no enrichments this week" in doc.body.lower(), (
        f"Empty-week message missing. Body: {doc.body!r}"
    )


def test_weekly_doc_renders_with_enrichments():
    """Populated-week doc shape: count line is correct and every
    lead summary appears in the body. The Phase 5 slash command
    parses these lines, so the layout is a contract."""

    summaries = [
        weekly_status.WeeklyLeadSummary(
            record_id="00Q5g00000XYZAbc",
            name="Acme GmbH",
            enriched_at="2026-05-12T09:00:00Z",
            angle_excerpt="Likely interested in stretch film migration.",
        ),
        weekly_status.WeeklyLeadSummary(
            record_id="00Q5g00000XYZDef",
            name="Globex Corp",
            enriched_at="2026-05-13T11:15:00Z",
            angle_excerpt="Sustainability mandate triggering RFP.",
        ),
    ]

    doc = weekly_status.compose_weekly_status_doc(
        week_label="2026-W19",
        lead_summaries=summaries,
        monthly_close_target_usd=500_000,
    )

    assert doc.lead_count == 2
    # Both leads' names appear in the body.
    assert "Acme GmbH" in doc.body
    assert "Globex Corp" in doc.body
    # The monthly close target is referenced so the operator can
    # eyeball pacing context against the dashboards.
    assert "$500,000" in doc.body or "500,000" in doc.body
