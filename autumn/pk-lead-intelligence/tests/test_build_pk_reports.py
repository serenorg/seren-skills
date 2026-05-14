"""Critical tests for Phase 3 reports + dashboards specs.

The PK Lead Dashboard, PK Opportunity Pipeline & Rolling Forecast
dashboard, and All Sources PK Leads report are consumed by Phase 4
(the daily cron) and by humans browsing Salesforce. Phase 3's job
is to lock the spec shapes — filter clauses, component counts, and
source-report wiring — so Phase 4 can rely on them.

Like provision_fields, the actual UI driving is exercised at the
operator checkpoint. These tests guard the contract.
"""

from __future__ import annotations

from scripts.sf import build_all_sources_leads_report as all_leads_report
from scripts.sf import build_pk_lead_dashboard as lead_dashboard
from scripts.sf import build_pk_opp_artifacts as opp_artifacts


# --------------------------------------------------------------------- #
# All Sources PK Leads report                                            #
# --------------------------------------------------------------------- #


def test_all_sources_report_spec_filters_on_packaging_true():
    """The cross-division gate lives in the report filter.

    If this filter ever stops scoping to PACKAGING__c=true, the
    Phase 4 cron will enrich non-PK Leads and write Notes onto the
    wrong division — the P0 mis-routing defect called out in
    SKILL.md. The contract is locked here.
    """

    spec = all_leads_report.ALL_SOURCES_PK_LEADS_REPORT_SPEC

    assert spec.title == "All Sources PK Leads"
    assert spec.report_type == "Leads"

    packaging_clauses = [
        c for c in spec.filters if c.field == "PACKAGING__c"
    ]
    assert len(packaging_clauses) == 1, (
        "Exactly one PACKAGING__c filter clause is required. "
        f"Got: {packaging_clauses}"
    )
    clause = packaging_clauses[0]
    assert clause.operator == "equals"
    # The Lightning report filter UI compares booleans as the literal
    # strings "true" / "false"; the value here must match what the
    # form accepts.
    assert clause.value == "true"

    # The skill reads the new custom Lead fields by API name when
    # the cron lands in Phase 4. Lock the column wiring now.
    assert "Activity_Gap_Days__c" in spec.columns
    assert "Last_Enrichment_At__c" in spec.columns


# --------------------------------------------------------------------- #
# PK Lead Dashboard                                                      #
# --------------------------------------------------------------------- #


def test_pk_lead_dashboard_spec_has_three_components():
    """Phase 3 deliverable: dashboard has exactly 3 components.

    Each must be sourced from the All Sources PK Leads report — the
    dashboard is the human surface for the same data the cron reads.
    """

    spec = lead_dashboard.PK_LEAD_DASHBOARD_SPEC

    assert spec.title == "PK Lead Dashboard"
    assert len(spec.components) == 3, (
        "Phase 3 contract: 3 components. "
        f"Got {len(spec.components)}: {[c.title for c in spec.components]}"
    )

    # Every component must read from the canonical PK report so the
    # dashboard and the cron agree on which Leads are in scope. A
    # component sourced from a different report would render
    # numbers that disagree with the Lead enrichment count.
    for component in spec.components:
        assert component.source_report == "All Sources PK Leads", (
            f"{component.title}: source_report must be "
            f"'All Sources PK Leads', got {component.source_report!r}"
        )


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

    assert spec.title == "PK Opportunity Pipeline & Rolling Forecast"
    assert len(spec.components) == 5, (
        "Phase 3 contract: 5 components. "
        f"Got {len(spec.components)}: {[c.title for c in spec.components]}"
    )

    titles = {c.title for c in spec.components}
    assert "Pacing vs Monthly Close Target" in titles, (
        "Pacing-vs-target component is the load-bearing one for the "
        f"Phase 4 weekly status doc. Component titles: {titles}"
    )
