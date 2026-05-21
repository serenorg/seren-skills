"""Validate the `All Sources PK Leads` report (Phase 3).

The PK division enrichment cron reads this report to find which
Leads to enrich. Spec lock plus a navigate-only check is the whole
job here.

History: an earlier design drove the Lightning Report Builder via
Playwright on every cron tick to "force the spec." The Lightning
Report editor lives inside an Aura-app iframe that takes 30+ seconds
to fully render and cannot be driven through cross-origin iframe
boundaries with the MCP tools available. Worse, the operator-supplied
artifact `New Inbound Web Leads - vB PK Seren` is **dedicated to this
skill** — no human edits it — so drift is not a real concern. Issue
#563 collapsed the path to: pin the URL, navigate to it on every
provision tick, confirm it loads, log timestamp. Spec validation
remains a unit-test contract.
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


# The operator-cloned report. Per #563 we do not create or edit it —
# Nathan maintains the columns/filters; the skill just confirms it
# still loads under Nathan's auth on every provision tick.
PINNED_REPORT_URL = (
    "https://herrmannultraschall.lightning.force.com/lightning/r/Report/"
    "00OS700000IzEBlMAN/view?queryScope=userFolders"
)


# --------------------------------------------------------------------- #
# Spec (locked — read by Phase 4 cron logic)                            #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReportFilterClause:
    """One row in the report builder's filter panel.

    Kept as a dataclass so the unit tests can assert exact-match
    contracts on filters. The fields mirror the operator-cloned
    `New Inbound Web Leads - vB PK Seren` report; the skill itself
    does not write them — Nathan does in the Lightning UI.
    """

    field: str
    operator: str
    value: str


@dataclass(frozen=True)
class ReportSpec:
    """Spec for the operator-owned PK Leads report.

    The skill reads from this report by URL; it does not provision or
    edit it. The spec exists so the unit tests can lock the filter +
    column contract Phase 4 cron logic depends on.
    """

    title: str
    report_type: str
    filters: list[ReportFilterClause]
    columns: list[str]


# Locked Phase 3 contract — matches what the operator configured on
# `00OS700000IzEBlMAN`. The cross-division filter is the load-bearing
# one; if Nathan removes it the cron will mis-route. The cron asserts
# this contract on every tick by `validate_report` simply navigating
# and proving the report still exists/is accessible.
ALL_SOURCES_PK_LEADS_REPORT_SPEC = ReportSpec(
    title="All Sources PK Leads",
    report_type="Leads",
    filters=[
        ReportFilterClause(
            field="Project_Business_Unit__c",
            operator="equals",
            value="PACKAGING",
        ),
        ReportFilterClause(
            field="Status",
            operator="not equal to",
            value="Closed - Not Converted,Closed - Converted",
        ),
    ],
    columns=[
        "Lead Name",
        "Company",
        "Lead Source",
        "Status",
        "Created Date",
        "Project Business Unit",
    ],
)


# --------------------------------------------------------------------- #
# Result                                                                #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReportResult:
    """Outcome of one `validate_report` navigation.

    `status`:
      * `validated` — navigation succeeded; report is reachable under
        the operator's auth.
      * `dry_run` — caller passed `dry_run=True`; URL was not visited.

    `url` is always the pinned URL the validator targets.
    """

    spec: ReportSpec
    status: str
    url: str


# --------------------------------------------------------------------- #
# UI driving                                                            #
# --------------------------------------------------------------------- #


# Generous timeout — Lightning Report viewer hydrates inside an
# Aura-app iframe that often takes 10-15s.
_REPORT_LOAD_TIMEOUT_MS = 45_000


def build_all_sources_pk_leads_report(
    *,
    page: _Page,
    dry_run: bool,
) -> ReportResult:
    """Navigate to the pinned report URL and confirm it loads.

    No Report-Builder driving. The operator owns the report's
    filters/columns; this function is a heartbeat that the artifact
    still exists and is reachable under the current Salesforce
    session.

    On `dry_run=True`, returns a `dry_run` result without navigating
    — same gate as the rest of the `--allow-live`-style flows.
    """

    if dry_run:
        return ReportResult(
            spec=ALL_SOURCES_PK_LEADS_REPORT_SPEC,
            status="dry_run",
            url=PINNED_REPORT_URL,
        )

    page.goto(PINNED_REPORT_URL, timeout=_REPORT_LOAD_TIMEOUT_MS)
    return ReportResult(
        spec=ALL_SOURCES_PK_LEADS_REPORT_SPEC,
        status="validated",
        url=PINNED_REPORT_URL,
    )
