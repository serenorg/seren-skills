"""Build the PK Opportunity Pipeline & Rolling Forecast dashboard.

Five components covering open pipeline, forecast pacing, and
rolling close-rate. The pacing-vs-target component is load-bearing
for the Phase 4 weekly status doc — the doc reads its numerator
directly. Removing it breaks the weekly doc.

The dashboard is sourced from three supporting reports the driver
also provisions:

* `PK Open Pipeline` — open Opportunities filtered to PK.
* `PK Won/Lost — Last 90 Days` — closed Opportunities for the
  rolling close-rate metric.
* `PK Won — This Month` — pacing numerator.

Idempotent by title at both the report and dashboard level.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from scripts.sf.build_all_sources_leads_report import ReportSpec, ReportFilterClause
from scripts.sf.build_pk_lead_dashboard import (
    DashboardComponentSpec,
    DashboardResult,
    DashboardSpec,
)


# --------------------------------------------------------------------- #
# Protocols                                                             #
# --------------------------------------------------------------------- #


class _Page(Protocol):
    """Subset of `playwright.sync_api.Page` the driver uses."""

    url: str

    def goto(self, url: str, *, timeout: int = ...) -> object: ...


# --------------------------------------------------------------------- #
# Supporting report specs                                                #
# --------------------------------------------------------------------- #


# Phase 4 will also enrich Opportunities, but Phase 3 only needs the
# reports so the dashboard has data to bind to. Each report carries
# a PK filter at the report layer so a dashboard component
# accidentally reused on another dashboard still respects the
# division boundary.
PK_OPEN_PIPELINE_REPORT_SPEC = ReportSpec(
    title="PK Open Pipeline",
    report_type="Opportunities",
    filters=[
        ReportFilterClause(field="Division__c", operator="equals", value="PK"),
        ReportFilterClause(field="IsClosed", operator="equals", value="false"),
    ],
    columns=[
        "Opportunity Name",
        "Account Name",
        "Stage",
        "Amount",
        "Close Date",
        "Owner",
    ],
)

PK_WON_LOST_90D_REPORT_SPEC = ReportSpec(
    title="PK Won/Lost — Last 90 Days",
    report_type="Opportunities",
    filters=[
        ReportFilterClause(field="Division__c", operator="equals", value="PK"),
        ReportFilterClause(field="IsClosed", operator="equals", value="true"),
        ReportFilterClause(
            field="CloseDate",
            operator="greater or equal",
            value="LAST_N_DAYS:90",
        ),
    ],
    columns=[
        "Opportunity Name",
        "Account Name",
        "Stage",
        "Amount",
        "Close Date",
        "IsWon",
    ],
)

PK_WON_THIS_MONTH_REPORT_SPEC = ReportSpec(
    title="PK Won — This Month",
    report_type="Opportunities",
    filters=[
        ReportFilterClause(field="Division__c", operator="equals", value="PK"),
        ReportFilterClause(field="IsWon", operator="equals", value="true"),
        ReportFilterClause(
            field="CloseDate",
            operator="equals",
            value="THIS_MONTH",
        ),
    ],
    columns=[
        "Opportunity Name",
        "Account Name",
        "Amount",
        "Close Date",
    ],
)


SUPPORTING_REPORT_SPECS: list[ReportSpec] = [
    PK_OPEN_PIPELINE_REPORT_SPEC,
    PK_WON_LOST_90D_REPORT_SPEC,
    PK_WON_THIS_MONTH_REPORT_SPEC,
]


# --------------------------------------------------------------------- #
# Dashboard spec                                                         #
# --------------------------------------------------------------------- #


# Locked Phase 3 contract: five components. The pacing-vs-target
# component is the load-bearing one for the Phase 4 weekly doc.
PK_OPP_PIPELINE_DASHBOARD_SPEC = DashboardSpec(
    title="PK Opportunity Pipeline & Rolling Forecast",
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
# UI driving (validated at operator checkpoint)                          #
# --------------------------------------------------------------------- #


def _find_dashboard_url_by_title(  # pragma: no cover
    page: _Page,
    title: str,
) -> Optional[str]:
    raise NotImplementedError(
        "Live Dashboards-list scan is validated at the Phase 3 "
        "operator checkpoint."
    )


def _drive_new_dashboard(  # pragma: no cover
    page: _Page,
    spec: DashboardSpec,
) -> str:
    raise NotImplementedError(
        "Live Dashboard Builder driving is validated at the Phase 3 "
        "operator checkpoint."
    )


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


def build_pk_opp_dashboard(
    *,
    page: _Page,
    dry_run: bool,
) -> DashboardResult:
    """Provision the PK Opportunity Pipeline dashboard (idempotent).

    Supporting reports are provisioned by callers (the agent's
    `_run_provision` seam) before this function is invoked, so that
    a missing dependency surfaces as a clear error rather than as
    a half-built dashboard.
    """

    existing_url = _find_dashboard_url_by_title(
        page, PK_OPP_PIPELINE_DASHBOARD_SPEC.title
    )
    if existing_url is not None:
        return DashboardResult(
            spec=PK_OPP_PIPELINE_DASHBOARD_SPEC,
            created=False,
            url=existing_url,
        )

    if dry_run:
        return DashboardResult(
            spec=PK_OPP_PIPELINE_DASHBOARD_SPEC,
            created=False,
            url=None,
        )

    url = _drive_new_dashboard(page, PK_OPP_PIPELINE_DASHBOARD_SPEC)
    return DashboardResult(
        spec=PK_OPP_PIPELINE_DASHBOARD_SPEC,
        created=True,
        url=url,
    )
