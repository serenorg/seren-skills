"""Weekly status doc generator (Phase 4).

Pure renderer for the Tuesday-morning Google Doc that summarizes
the past 7 days of PK Lead enrichment activity. Built as a pure
function so it is fully unit-testable; the upload + share step
lives in `scripts/integrations/google_drive.py`.

The layout is operator-facing: the Phase 5 `/pk-status` slash
command parses these lines to surface the most recent doc URL
and the lead count. Section order and the per-lead line shape are
contracts.
"""

from __future__ import annotations

from dataclasses import dataclass


# --------------------------------------------------------------------- #
# Types                                                                 #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class WeeklyLeadSummary:
    """One Lead's enrichment summary for inclusion in the weekly doc.

    `enriched_at` is ISO-8601 UTC. `angle_excerpt` is the first
    ~120 characters of the first ultrasonic-welding angle on the
    rendered Note — enough for the operator to remember what the
    Note said without re-opening every Lead in Salesforce. Renamed
    from `hypothesis_excerpt` per #766; the field's job is the same.
    """

    record_id: str
    name: str
    enriched_at: str
    angle_excerpt: str


@dataclass(frozen=True)
class WeeklyStatusDoc:
    """The rendered weekly status doc, ready for Drive upload.

    `body` is plain text rendered in the locked layout; the Drive
    upload step copies it verbatim into a Google Doc.

    `lead_count` and `enrichment_count` are surfaced separately
    even though they coincide today, so the Phase 5 slash command
    can read them without parsing the body. When a Lead is
    re-enriched mid-week the two will diverge.
    """

    title: str
    body: str
    lead_count: int
    enrichment_count: int
    week_window: str


# --------------------------------------------------------------------- #
# Renderer                                                              #
# --------------------------------------------------------------------- #


def _format_target(monthly_close_target_usd: int) -> str:
    """Render the monthly close target with a thousands separator.

    Locale-safe: Python's `:,` format spec is fine for US dollars,
    which is the only currency the skill supports.
    """

    return f"${monthly_close_target_usd:,.0f}"


def compose_weekly_status_doc(
    *,
    week_label: str,
    lead_summaries: list[WeeklyLeadSummary],
    monthly_close_target_usd: int,
    week_window: str = "",
) -> WeeklyStatusDoc:
    """Render the weekly status doc.

    The empty-week case ("no enrichments this week") is explicitly
    distinct from the populated case so the operator can tell a
    successful empty run from a failed run.

    `week_label` is the operator-facing ISO week tag (e.g.
    `2026-W19`). `week_window` is an optional human-readable date
    range (e.g. `2026-05-11 to 2026-05-17`) for the body header;
    when empty, the label is used as the window.
    """

    title = f"PK Weekly Status — {week_label}"
    window = week_window or week_label
    target_str = _format_target(monthly_close_target_usd)

    lines: list[str] = [
        title,
        "",
        f"Week: {window}",
        f"Monthly close target: {target_str}",
        "",
    ]

    if not lead_summaries:
        lines.extend(
            [
                "PK Leads enriched this week: 0",
                "",
                "No enrichments this week.",
                "",
                "If you expected enrichments, check the daily cron's "
                "last-run summary for failures and re-run any blocked "
                "days manually.",
            ]
        )
    else:
        lines.extend(
            [
                f"PK Leads enriched this week: {len(lead_summaries)}",
                "",
                "Enrichment summary:",
            ]
        )
        for summary in lead_summaries:
            lines.append(
                f"- {summary.name} ({summary.record_id}) — "
                f"enriched {summary.enriched_at}"
            )
            if summary.angle_excerpt:
                lines.append(f"  Angle: {summary.angle_excerpt}")
        lines.extend(
            [
                "",
                "Pacing context:",
                f"- Monthly close target: {target_str}",
                "- See `PK Opportunity Pipeline & Rolling Forecast` "
                "dashboard for the live pacing component.",
            ]
        )

    body = "\n".join(lines) + "\n"

    return WeeklyStatusDoc(
        title=title,
        body=body,
        lead_count=len(lead_summaries),
        enrichment_count=len(lead_summaries),
        week_window=window,
    )
