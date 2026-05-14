"""Critical tests for scripts/sf/provision_fields.py.

Phase 3 introduces three custom Lead fields. The spec must be locked
because Phase 4 reads them by API name — a typo here is a silent
data-routing bug, not a startup error. Idempotency is also required
because the cron may re-run provision on a partially-set-up org.

We do not exercise Lightning itself. The driving code is thin
orchestration over the `Page` protocol; live correctness is
validated at the operator checkpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from scripts.sf import provision_fields


# --------------------------------------------------------------------- #
# Fakes                                                                 #
# --------------------------------------------------------------------- #


@dataclass
class FakePage:
    """Stand-in for an authenticated Playwright Page.

    Records every call so tests can assert the driver visited the
    right surface in the right order. The `existing_field_api_names`
    seam controls what `_list_existing_lead_field_api_names` returns
    so tests can model fully-provisioned vs partially-provisioned
    orgs without writing a Playwright fake of the Object Manager list.
    """

    existing_field_api_names: list[str] = field(default_factory=list)
    call_log: list[tuple[str, tuple]] = field(default_factory=list)
    url: str = ""

    def goto(self, url, *, timeout: int = 0):
        self.call_log.append(("goto", (url,)))
        self.url = url


# --------------------------------------------------------------------- #
# Spec contract                                                         #
# --------------------------------------------------------------------- #


def test_lead_field_specs_match_phase3_contract():
    """Phase 4 reads these fields by API name. The contract is locked:

    * `PACKAGING__c` — Checkbox; the cross-division gate.
    * `Last_Enrichment_At__c` — DateTime; idempotency key for re-enrichment.
    * `Activity_Gap_Days__c` — Number; feeds the PK Lead Dashboard.

    Field count, API names, and types must all match — a rename
    here is a Phase 4 read-path break, not a cosmetic edit.
    """

    specs = provision_fields.LEAD_FIELD_SPECS
    assert len(specs) == 3, (
        "Phase 3 contract: exactly 3 custom Lead fields. "
        f"Got {len(specs)}: {[s.api_name for s in specs]}"
    )

    by_api = {s.api_name: s for s in specs}

    assert by_api["PACKAGING__c"].field_type == "Checkbox"
    # Salesforce's New Custom Field wizard radio renders as the
    # literal label "Date/Time" — the selector builder matches by
    # that label verbatim. A drift to "DateTime" would silently
    # fail to click any radio at all.
    assert by_api["Last_Enrichment_At__c"].field_type == "Date/Time"
    assert by_api["Activity_Gap_Days__c"].field_type == "Number"

    # Every field must carry a non-empty description — Object Manager
    # accepts blank descriptions but they look unprofessional to an
    # admin reviewing the schema later. Belt-and-suspenders.
    for spec in specs:
        assert spec.description, f"{spec.api_name} description is empty"


# --------------------------------------------------------------------- #
# Idempotency                                                           #
# --------------------------------------------------------------------- #


def test_provision_skips_when_all_fields_exist(monkeypatch):
    """Re-running provision on a fully-provisioned org is a no-op.

    The cron may re-trigger provision (e.g. after a host migration);
    if every field already exists by API name, the driver must not
    open the New-field form for any of them. A skipped run reports
    `created=[]` and `skipped=[all three specs]`.
    """

    page = FakePage(
        existing_field_api_names=[
            "PACKAGING__c",
            "Last_Enrichment_At__c",
            "Activity_Gap_Days__c",
        ]
    )

    drive_calls: list[provision_fields.LeadFieldSpec] = []

    monkeypatch.setattr(
        provision_fields,
        "_list_existing_lead_field_api_names",
        lambda page: page.existing_field_api_names,
    )
    monkeypatch.setattr(
        provision_fields,
        "_drive_new_field",
        lambda page, spec: drive_calls.append(spec),
    )

    result = provision_fields.provision_lead_fields(page=page, dry_run=False)

    assert drive_calls == [], (
        f"No fields should be driven when all exist; got {drive_calls}"
    )
    assert result.created == []
    assert {s.api_name for s in result.skipped} == {
        "PACKAGING__c",
        "Last_Enrichment_At__c",
        "Activity_Gap_Days__c",
    }


def test_provision_creates_only_missing_fields(monkeypatch):
    """Partial provisioning: two of three exist, one is missing.

    The driver must drive the New-field form exactly once — for the
    one missing field — and report it as created. The two existing
    fields land in `skipped` without touching the UI.
    """

    page = FakePage(
        existing_field_api_names=[
            "PACKAGING__c",
            "Last_Enrichment_At__c",
            # Activity_Gap_Days__c is missing
        ]
    )

    drive_calls: list[provision_fields.LeadFieldSpec] = []

    monkeypatch.setattr(
        provision_fields,
        "_list_existing_lead_field_api_names",
        lambda page: page.existing_field_api_names,
    )
    monkeypatch.setattr(
        provision_fields,
        "_drive_new_field",
        lambda page, spec: drive_calls.append(spec),
    )

    result = provision_fields.provision_lead_fields(page=page, dry_run=False)

    assert len(drive_calls) == 1
    assert drive_calls[0].api_name == "Activity_Gap_Days__c"
    assert [s.api_name for s in result.created] == ["Activity_Gap_Days__c"]
    assert {s.api_name for s in result.skipped} == {
        "PACKAGING__c",
        "Last_Enrichment_At__c",
    }


def test_provision_dry_run_plans_without_driving(monkeypatch):
    """`dry_run=True` reports what would be created without driving.

    The Phase 3 contract is `--allow-live` gates the actual UI
    driving; dry-run produces the plan for operator review and
    never touches the New-field form.
    """

    page = FakePage(existing_field_api_names=[])

    drive_calls: list[provision_fields.LeadFieldSpec] = []

    monkeypatch.setattr(
        provision_fields,
        "_list_existing_lead_field_api_names",
        lambda page: page.existing_field_api_names,
    )
    monkeypatch.setattr(
        provision_fields,
        "_drive_new_field",
        lambda page, spec: drive_calls.append(spec),
    )

    result = provision_fields.provision_lead_fields(page=page, dry_run=True)

    assert drive_calls == [], "dry_run must not drive the UI"
    assert len(result.planned) == 3
    assert result.created == []
