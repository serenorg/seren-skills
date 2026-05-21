"""Validate the `PK Lead Dashboard` (Phase 3).

Operator-owned dashboard `01ZS7000004KhcnMAC` — Nathan maintains the
components; the skill just confirms it still loads on every provision
tick. Same architectural rationale as
`build_all_sources_leads_report.py`: the Lightning Dashboard editor
sits inside an Aura-app iframe that cannot be cleanly driven every
cron tick, and the artifact is dedicated to this skill so drift is
not a real concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


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
    "01ZS7000004KhcnMAC/view"
)


# --------------------------------------------------------------------- #
# Spec (locked — read by Phase 4 weekly doc logic)                       #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class DashboardComponentSpec:
    """One Lightning Dashboard component.

    Mirrors the operator-configured components on
    `01ZS7000004KhcnMAC` (`PK Inbound Web Lead and Activity Tracking
    - SerenAI`). The skill does not write these — Nathan does.
    """

    title: str
    component_type: str
    source_report: str
    grouping: Optional[str]
    aggregate: str


@dataclass(frozen=True)
class DashboardSpec:
    """Spec for the operator-owned PK Lead Dashboard."""

    title: str
    components: list[DashboardComponentSpec]


# Locked Phase 3 contract — matches Nathan's configuration. Three
# components, all sourced from `All Sources PK Leads` so the
# dashboard's totals agree with the cron's enrichment scope.
PK_LEAD_DASHBOARD_SPEC = DashboardSpec(
    title="PK Inbound Web Lead and Activity Tracking - SerenAI",
    components=[
        DashboardComponentSpec(
            title="New PK Leads This Week",
            component_type="metric",
            source_report="All Sources PK Leads",
            grouping=None,
            aggregate="count",
        ),
        DashboardComponentSpec(
            title="PK Leads by Source",
            component_type="horizontal_bar",
            source_report="All Sources PK Leads",
            grouping="Lead Source",
            aggregate="count",
        ),
        DashboardComponentSpec(
            title="PK Leads by Activity Gap",
            component_type="vertical_bar",
            source_report="All Sources PK Leads",
            grouping="Days Since Last Activity",
            aggregate="count",
        ),
    ],
)


# --------------------------------------------------------------------- #
# Result                                                                #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class DashboardResult:
    """Outcome of one `validate_dashboard` navigation.

    `status`:
      * `validated` — navigation succeeded; dashboard reachable.
      * `dry_run` — caller passed `dry_run=True`; URL not visited.
    """

    spec: DashboardSpec
    status: str
    url: str


# --------------------------------------------------------------------- #
# UI driving                                                            #
# --------------------------------------------------------------------- #


_DASHBOARD_LOAD_TIMEOUT_MS = 45_000


def build_pk_lead_dashboard(
    *,
    page: _Page,
    dry_run: bool,
) -> DashboardResult:
    """Navigate to the pinned dashboard URL and confirm it loads.

    No Dashboard-Builder driving. Same reasoning as the report
    validator: artifact is operator-owned, dedicated to this skill,
    no manual drift expected.
    """

    if dry_run:
        return DashboardResult(
            spec=PK_LEAD_DASHBOARD_SPEC,
            status="dry_run",
            url=PINNED_DASHBOARD_URL,
        )

    page.goto(PINNED_DASHBOARD_URL, timeout=_DASHBOARD_LOAD_TIMEOUT_MS)
    return DashboardResult(
        spec=PK_LEAD_DASHBOARD_SPEC,
        status="validated",
        url=PINNED_DASHBOARD_URL,
    )
