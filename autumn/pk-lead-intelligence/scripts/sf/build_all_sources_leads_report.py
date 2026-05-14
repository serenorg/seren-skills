"""Build the `All Sources PK Leads` report via Lightning Report Builder.

The PK division enrichment cron (Phase 4) reads this report row-by-
row to find which Leads to enrich. The filter `PACKAGING__c = true`
is the cross-division gate that lives in the report itself — a
mis-routed enrichment that lands a Note on a non-PK Lead is the P0
defect called out in `SKILL.md`.

Idempotent by title: a report already named `All Sources PK Leads`
is reused, not duplicated.

Phase 3 implements the spec lock and the dry-run plan. The actual
Lightning Report Builder driving is validated at the operator
checkpoint with the operator watching the headful run.
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
class ReportFilterClause:
    """One row in the report builder's filter panel.

    `value` is the literal string the Lightning filter UI accepts.
    Booleans are compared as the strings `"true"` / `"false"`;
    multi-value comparisons like NOT EQUAL pass a comma-separated
    list.
    """

    field: str
    operator: str
    value: str


@dataclass(frozen=True)
class ReportSpec:
    """One Lightning report.

    `report_type` matches the visible label on step 1 of the New
    Report wizard ("Leads", "Opportunities", etc.). `columns`
    lists field labels exactly as they appear in the report builder's
    column picker.
    """

    title: str
    report_type: str
    filters: list[ReportFilterClause]
    columns: list[str]


# Locked Phase 3 contract. The PACKAGING__c filter is the
# cross-division gate; removing or relaxing it routes Notes to the
# wrong division.
ALL_SOURCES_PK_LEADS_REPORT_SPEC = ReportSpec(
    title="All Sources PK Leads",
    report_type="Leads",
    filters=[
        ReportFilterClause(
            field="PACKAGING__c",
            operator="equals",
            value="true",
        ),
        # The cron only enriches Leads still in flight. Closed states
        # are excluded at the report layer so the cron never has to
        # re-filter them client-side.
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
        "Activity_Gap_Days__c",
        "Last_Enrichment_At__c",
    ],
)


# --------------------------------------------------------------------- #
# Result                                                                #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReportResult:
    """Bundle returned from one `build_all_sources_pk_leads_report` call.

    `created=False` when the report already existed and the driver
    reused it. `url` is populated when the driver was able to
    capture the report's permalink (always populated for created
    reports; populated for reused reports when the search returns
    a hit).
    """

    spec: ReportSpec
    created: bool
    url: Optional[str]


# --------------------------------------------------------------------- #
# UI driving (validated at operator checkpoint)                          #
# --------------------------------------------------------------------- #


def _find_report_url_by_title(  # pragma: no cover
    page: _Page,
    title: str,
) -> Optional[str]:
    """Search the Reports list for `title`. Return its URL or None.

    Live execution navigates to `/lightning/o/Report/home`, types
    `title` into the search box, and resolves the first matching
    row's href. Tests monkeypatch this seam.
    """

    raise NotImplementedError(
        "Live Reports-list scan is validated at the Phase 3 "
        "operator checkpoint."
    )


def _drive_new_report(  # pragma: no cover
    page: _Page,
    spec: ReportSpec,
) -> str:
    """Drive the New Report wizard. Return the saved report's URL.

    Live execution: New Report → pick report_type → add columns →
    add filter clauses → Save As (title). Tests monkeypatch.
    """

    raise NotImplementedError(
        "Live Report Builder driving is validated at the Phase 3 "
        "operator checkpoint."
    )


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


def build_all_sources_pk_leads_report(
    *,
    page: _Page,
    dry_run: bool,
) -> ReportResult:
    """Provision the `All Sources PK Leads` report (idempotent).

    Behaviour:

    * If a report titled `All Sources PK Leads` already exists, the
      driver reuses it and reports `created=False`.
    * Otherwise on a dry-run, the driver returns the spec without
      driving the wizard (`url=None`).
    * Otherwise the driver drives the New Report wizard end-to-end
      and returns the saved URL.
    """

    existing_url = _find_report_url_by_title(
        page, ALL_SOURCES_PK_LEADS_REPORT_SPEC.title
    )
    if existing_url is not None:
        return ReportResult(
            spec=ALL_SOURCES_PK_LEADS_REPORT_SPEC,
            created=False,
            url=existing_url,
        )

    if dry_run:
        return ReportResult(
            spec=ALL_SOURCES_PK_LEADS_REPORT_SPEC,
            created=False,
            url=None,
        )

    url = _drive_new_report(page, ALL_SOURCES_PK_LEADS_REPORT_SPEC)
    return ReportResult(
        spec=ALL_SOURCES_PK_LEADS_REPORT_SPEC,
        created=True,
        url=url,
    )
