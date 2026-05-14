"""Build the three-component PK Lead Dashboard.

The dashboard is the human-readable surface for the same data the
Phase 4 cron consumes. All three components source from the
`All Sources PK Leads` report so the dashboard's totals always
agree with the cron's enrichment scope.

Idempotent by title: a dashboard already named `PK Lead Dashboard`
is reused, not duplicated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


# --------------------------------------------------------------------- #
# Protocols                                                             #
# --------------------------------------------------------------------- #


class _Page(Protocol):
    """Subset of `playwright.sync_api.Page` the driver uses."""

    url: str

    def goto(self, url: str, *, timeout: int = ...) -> object: ...


# --------------------------------------------------------------------- #
# Spec                                                                  #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class DashboardComponentSpec:
    """One component on a Lightning Dashboard.

    `component_type` matches the visible chart-picker label in the
    Dashboard Builder. Phase 3 uses `metric`, `horizontal_bar`, and
    `vertical_bar`.
    """

    title: str
    component_type: str
    source_report: str
    grouping: Optional[str]
    aggregate: str


@dataclass(frozen=True)
class DashboardSpec:
    """One Lightning Dashboard.

    Order matters: `components` lists components in the order they
    will appear on the dashboard grid (row-major, top-left first).
    """

    title: str
    components: list[DashboardComponentSpec]


# Locked Phase 3 contract. Three components, all sourcing from the
# `All Sources PK Leads` report so the totals match Phase 4 cron
# scope.
PK_LEAD_DASHBOARD_SPEC = DashboardSpec(
    title="PK Lead Dashboard",
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
            grouping="Activity_Gap_Days__c",
            aggregate="count",
        ),
    ],
)


# --------------------------------------------------------------------- #
# Result                                                                #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class DashboardResult:
    """Bundle returned from a dashboard-builder call.

    `created=False` when the dashboard already existed and the
    driver reused it.
    """

    spec: DashboardSpec
    created: bool
    url: Optional[str]


# --------------------------------------------------------------------- #
# UI driving (validated at operator checkpoint)                          #
# --------------------------------------------------------------------- #


def _find_dashboard_url_by_title(  # pragma: no cover
    page: _Page,
    title: str,
) -> Optional[str]:
    """Search the Dashboards list for `title`. Return its URL or None."""

    raise NotImplementedError(
        "Live Dashboards-list scan is validated at the Phase 3 "
        "operator checkpoint."
    )


def _drive_new_dashboard(  # pragma: no cover
    page: _Page,
    spec: DashboardSpec,
) -> str:
    """Drive the New Dashboard builder. Return the saved URL."""

    raise NotImplementedError(
        "Live Dashboard Builder driving is validated at the Phase 3 "
        "operator checkpoint."
    )


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


def build_pk_lead_dashboard(
    *,
    page: _Page,
    dry_run: bool,
) -> DashboardResult:
    """Provision the `PK Lead Dashboard` (idempotent)."""

    existing_url = _find_dashboard_url_by_title(
        page, PK_LEAD_DASHBOARD_SPEC.title
    )
    if existing_url is not None:
        return DashboardResult(
            spec=PK_LEAD_DASHBOARD_SPEC,
            created=False,
            url=existing_url,
        )

    if dry_run:
        return DashboardResult(
            spec=PK_LEAD_DASHBOARD_SPEC,
            created=False,
            url=None,
        )

    url = _drive_new_dashboard(page, PK_LEAD_DASHBOARD_SPEC)
    return DashboardResult(
        spec=PK_LEAD_DASHBOARD_SPEC,
        created=True,
        url=url,
    )
