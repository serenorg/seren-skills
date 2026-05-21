"""Critical tests for Phase 3 reports + dashboards specs.

The PK Lead Dashboard, PK Opportunity Pipeline & Rolling Forecast
dashboard, and All Sources PK Leads report are consumed by Phase 4
(the daily cron) and by humans browsing Salesforce. Phase 3's job
is to lock the spec shapes — filter clauses, component counts, and
source-report wiring — so Phase 4 can rely on them.

Issue #563 collapsed the editor-driving paths to navigate-only
validators (the artifacts are operator-owned, dedicated to this
skill, and live inside Aura-app iframes that are too fragile to
drive every cron tick). The spec contract below is still the
load-bearing piece — the cron's cross-division gate depends on
Nathan keeping the filter intact.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from scripts.sf import build_all_sources_leads_report as all_leads_report
from scripts.sf import build_pk_lead_dashboard as lead_dashboard
from scripts.sf import build_pk_opp_artifacts as opp_artifacts


# --------------------------------------------------------------------- #
# Fakes                                                                 #
# --------------------------------------------------------------------- #


@dataclass
class FakePage:
    """Stand-in Page that records navigation attempts."""

    goto_calls: list[str] = field(default_factory=list)
    url: str = ""

    def goto(self, url: str, *, timeout: int = 0) -> None:
        self.goto_calls.append(url)
        self.url = url


# --------------------------------------------------------------------- #
# All Sources PK Leads report                                            #
# --------------------------------------------------------------------- #


def test_all_sources_report_spec_filters_on_packaging():
    """The cross-division gate lives in the report filter.

    Issue #563: the field migrated from `PACKAGING__c` (custom field
    the operator could not create) to `Project_Business_Unit__c`
    (Lightning standard custom field already on the Lead object,
    value `PACKAGING` for the PK division). If this filter ever
    stops scoping to PK, the cron will enrich non-PK Leads and
    write Notes onto the wrong division — the P0 mis-routing
    defect called out in SKILL.md. The contract is locked here.
    """

    spec = all_leads_report.ALL_SOURCES_PK_LEADS_REPORT_SPEC

    assert spec.title == "All Sources PK Leads"
    assert spec.report_type == "Leads"

    pk_clauses = [
        c for c in spec.filters if c.field == "Project_Business_Unit__c"
    ]
    assert len(pk_clauses) == 1, (
        "Exactly one Project_Business_Unit__c filter clause is "
        f"required. Got: {pk_clauses}"
    )
    clause = pk_clauses[0]
    assert clause.operator == "equals"
    assert clause.value == "PACKAGING"

    # Operator-facing column wiring. Nathan owns the actual report;
    # this spec describes the contract the cron expects to see.
    assert "Project Business Unit" in spec.columns


def test_all_sources_report_validate_navigates_to_pinned_url():
    """Live validation hits the pinned operator-owned URL."""

    page = FakePage()
    result = all_leads_report.build_all_sources_pk_leads_report(
        page=page, dry_run=False
    )

    assert result.status == "validated"
    assert result.url == all_leads_report.PINNED_REPORT_URL
    assert page.goto_calls == [all_leads_report.PINNED_REPORT_URL]


def test_all_sources_report_dry_run_does_not_navigate():
    """Dry-run returns the spec without driving Playwright."""

    page = FakePage()
    result = all_leads_report.build_all_sources_pk_leads_report(
        page=page, dry_run=True
    )

    assert result.status == "dry_run"
    assert result.url == all_leads_report.PINNED_REPORT_URL
    assert page.goto_calls == [], (
        "dry_run must not touch the live URL"
    )


# --------------------------------------------------------------------- #
# PK Lead Dashboard                                                      #
# --------------------------------------------------------------------- #


def test_pk_lead_dashboard_spec_has_three_components():
    """Phase 3 deliverable: dashboard has exactly 3 components.

    Each must be sourced from the All Sources PK Leads report — the
    dashboard is the human surface for the same data the cron reads.
    """

    spec = lead_dashboard.PK_LEAD_DASHBOARD_SPEC

    assert spec.title == "PK Inbound Web Lead and Activity Tracking - SerenAI"
    assert len(spec.components) == 3, (
        "Phase 3 contract: 3 components. "
        f"Got {len(spec.components)}: {[c.title for c in spec.components]}"
    )

    # Every component must read from the canonical PK report so the
    # dashboard and the cron agree on which Leads are in scope.
    for component in spec.components:
        assert component.source_report == "All Sources PK Leads", (
            f"{component.title}: source_report must be "
            f"'All Sources PK Leads', got {component.source_report!r}"
        )


def test_pk_lead_dashboard_validate_navigates_to_pinned_url():
    page = FakePage()
    result = lead_dashboard.build_pk_lead_dashboard(page=page, dry_run=False)

    assert result.status == "validated"
    assert result.url == lead_dashboard.PINNED_DASHBOARD_URL
    assert page.goto_calls == [lead_dashboard.PINNED_DASHBOARD_URL]


def test_pk_lead_dashboard_dry_run_does_not_navigate():
    page = FakePage()
    result = lead_dashboard.build_pk_lead_dashboard(page=page, dry_run=True)

    assert result.status == "dry_run"
    assert page.goto_calls == []


# --------------------------------------------------------------------- #
# PK Opportunity Pipeline & Rolling Forecast dashboard                   #
# --------------------------------------------------------------------- #


def test_pk_opp_dashboard_spec_has_five_components_with_pacing():
    """Phase 3 deliverable: 5 components on the Opportunity dashboard.

    The pacing component is the load-bearing one — the weekly status
    doc (Phase 4) references it directly to surface "are we tracking
    to monthly_close_target_usd?". Without it the weekly doc has no
    numerator. Lock its presence.
    """

    spec = opp_artifacts.PK_OPP_PIPELINE_DASHBOARD_SPEC

    assert (
        spec.title
        == "PK Inbound Web Lead and Opportunity Tracking - SerenAI"
    )
    assert len(spec.components) == 5, (
        "Phase 3 contract: 5 components. "
        f"Got {len(spec.components)}: {[c.title for c in spec.components]}"
    )

    titles = {c.title for c in spec.components}
    assert "Pacing vs Monthly Close Target" in titles, (
        "Pacing-vs-target component is the load-bearing one for the "
        f"Phase 4 weekly status doc. Component titles: {titles}"
    )


def test_pk_opp_dashboard_validate_navigates_to_pinned_url():
    page = FakePage()
    result = opp_artifacts.build_pk_opp_dashboard(page=page, dry_run=False)

    assert result.status == "validated"
    assert result.url == opp_artifacts.PINNED_DASHBOARD_URL
    assert page.goto_calls == [opp_artifacts.PINNED_DASHBOARD_URL]


def test_pk_opp_dashboard_dry_run_does_not_navigate():
    page = FakePage()
    result = opp_artifacts.build_pk_opp_dashboard(page=page, dry_run=True)

    assert result.status == "dry_run"
    assert page.goto_calls == []
