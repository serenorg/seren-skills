"""Validate the PK Opportunity Pipeline dashboard (Phase 3).

Operator-owned dashboard `01ZS7000004KhePMAS` — Nathan maintains the
components; the skill just confirms it still loads on every provision
tick. Same architectural rationale as
`build_all_sources_leads_report.py` and
`build_pk_lead_dashboard.py`: the Lightning Dashboard editor sits
inside an Aura-app iframe that cannot be cleanly driven every cron
tick, and the artifact is dedicated to this skill so drift is not a
real concern.

The pacing-vs-target component is the load-bearing one for the Phase 4
weekly status doc — the doc reads its numerator directly. Spec lock
below.
"""

from __future__ import annotations

from typing import Protocol

from scripts.sf.build_pk_lead_dashboard import (
    DashboardComponentSpec,
    DashboardResult,
    DashboardSpec,
)


# --------------------------------------------------------------------- #
# Protocols                                                             #
# --------------------------------------------------------------------- #


class _Page(Protocol):
    """Subset of `playwright.sync_api.Page` the validator uses."""

    url: str

    def goto(self, url: str, *, timeout: int = ...) -> object: ...


# --------------------------------------------------------------------- #
# Pinned artifact                                                        #
# --------------------------------------------------------------------- #


PINNED_DASHBOARD_URL = (
    "https://herrmannultraschall.lightning.force.com/lightning/r/Dashboard/"
    "01ZS7000004KhePMAS/view"
)


# --------------------------------------------------------------------- #
# Spec (locked — pacing component is load-bearing for weekly doc)        #
# --------------------------------------------------------------------- #


# Locked Phase 3 contract: five components. The pacing-vs-target
# component is the load-bearing one for the Phase 4 weekly doc.
PK_OPP_PIPELINE_DASHBOARD_SPEC = DashboardSpec(
    title="PK Inbound Web Lead and Opportunity Tracking - SerenAI",
    components=[
        DashboardComponentSpec(
            title="Open Pipeline by Stage",
            component_type="horizontal_bar",
            source_report="PK Open Pipeline",
            grouping="Stage",
            aggregate="sum",
        ),
        DashboardComponentSpec(
            title="Pipeline by Close Month",
            component_type="vertical_bar",
            source_report="PK Open Pipeline",
            grouping="CALENDAR_MONTH(CloseDate)",
            aggregate="sum",
        ),
        DashboardComponentSpec(
            title="Rolling 90-Day Close Rate",
            component_type="metric",
            source_report="PK Won/Lost — Last 90 Days",
            grouping=None,
            aggregate="ratio",
        ),
        DashboardComponentSpec(
            title="Avg Days in Stage",
            component_type="horizontal_bar",
            source_report="PK Open Pipeline",
            grouping="Stage",
            aggregate="avg",
        ),
        DashboardComponentSpec(
            title="Pacing vs Monthly Close Target",
            component_type="metric",
            source_report="PK Won — This Month",
            grouping=None,
            aggregate="sum",
        ),
    ],
)


# --------------------------------------------------------------------- #
# UI driving                                                            #
# --------------------------------------------------------------------- #


_DASHBOARD_LOAD_TIMEOUT_MS = 45_000


def build_pk_opp_dashboard(
    *,
    page: _Page,
    dry_run: bool,
) -> DashboardResult:
    """Navigate to the pinned dashboard URL and confirm it loads.

    No Dashboard-Builder driving. Same reasoning as the report and
    Lead-dashboard validators: artifact is operator-owned, dedicated
    to this skill, no manual drift expected.
    """

    if dry_run:
        return DashboardResult(
            spec=PK_OPP_PIPELINE_DASHBOARD_SPEC,
            status="dry_run",
            url=PINNED_DASHBOARD_URL,
        )

    page.goto(PINNED_DASHBOARD_URL, timeout=_DASHBOARD_LOAD_TIMEOUT_MS)
    return DashboardResult(
        spec=PK_OPP_PIPELINE_DASHBOARD_SPEC,
        status="validated",
        url=PINNED_DASHBOARD_URL,
    )
