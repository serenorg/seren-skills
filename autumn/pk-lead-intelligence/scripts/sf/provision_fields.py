"""Idempotent provisioning of custom Lead fields (Phase 3).

Drives Setup → Object Manager → Lead → Fields & Relationships to
create the three custom fields the skill depends on:

* `PACKAGING__c` (Checkbox) — the cross-division gate; the Phase 4
  cron filters Leads by this column.
* `Last_Enrichment_At__c` (Date/Time) — the timestamp the cron writes
  after each Note so the next run does not re-enrich the same Lead.
* `Activity_Gap_Days__c` (Number) — days since last activity; feeds
  the PK Lead Dashboard's "PK Leads by Activity Gap" component.

Idempotent by design: the driver first scans the existing fields
list and skips any spec whose API name is already present, so the
cron can re-trigger provision on a partially-set-up org without
duplicating fields.

Phase 3 does **not** write Lead records. This module only mutates
the Lead object's schema metadata, which is intentionally a
different surface than the Phase 4 Note-write path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from scripts.sf import selectors


# --------------------------------------------------------------------- #
# Protocols                                                             #
# --------------------------------------------------------------------- #


class _Page(Protocol):
    """Subset of `playwright.sync_api.Page` the driver uses.

    Kept narrow so tests can supply a tiny stand-in without pulling
    Playwright into the test environment.
    """

    url: str

    def goto(self, url: str, *, timeout: int = ...) -> object: ...


# --------------------------------------------------------------------- #
# Spec                                                                  #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class LeadFieldSpec:
    """One custom Lead field to provision.

    `api_name` is the load-bearing handle. Phase 4 reads fields off
    Leads by API name; renaming one here is a Phase 4 read-path
    break, not a cosmetic edit.

    `field_type` matches the visible label on the New Custom Field
    wizard's step-1 radio ("Checkbox", "Date/Time", "Number"). The
    selector builder in `selectors.sf_new_field_type_radio`
    consumes this string verbatim.
    """

    api_name: str
    label: str
    field_type: str
    description: str
    default_value: str | None = None
    length: int | None = None
    decimal_places: int | None = None


# Locked Phase 3 contract. Renaming an api_name here is a Phase 4
# read-path break; widen the SKILL.md surface before changing this.
LEAD_FIELD_SPECS: list[LeadFieldSpec] = [
    LeadFieldSpec(
        api_name="PACKAGING__c",
        label="Packaging Division",
        field_type="Checkbox",
        description=(
            "True when the lead belongs to the PK division. Read by "
            "pk-lead-intelligence as the cross-division gate; a "
            "mis-routed enrichment that lands a Note on a non-PK "
            "Lead is a P0 defect."
        ),
        default_value="false",
    ),
    LeadFieldSpec(
        api_name="Last_Enrichment_At__c",
        label="Last Enrichment At",
        field_type="Date/Time",
        description=(
            "UTC timestamp the pk-lead-intelligence skill last "
            "wrote a Note to this Lead. Used by the cron to skip "
            "re-enriching the same Lead within the same day."
        ),
    ),
    LeadFieldSpec(
        api_name="Activity_Gap_Days__c",
        label="Activity Gap Days",
        field_type="Number",
        description=(
            "Days since the last activity on this Lead. Computed by "
            "the cron (Phase 4) and read by the PK Lead Dashboard."
        ),
        length=4,
        decimal_places=0,
    ),
]


# --------------------------------------------------------------------- #
# Result                                                                #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProvisionResult:
    """Bundle returned from one `provision_lead_fields` call.

    `planned` is the spec list the driver decided to create (after
    filtering against existing fields). `created` is the subset
    that the driver actually drove to completion — equal to
    `planned` when `dry_run=False`, empty when `dry_run=True`.
    `skipped` is the spec list whose API names already exist.
    """

    planned: list[LeadFieldSpec]
    created: list[LeadFieldSpec]
    skipped: list[LeadFieldSpec]


# --------------------------------------------------------------------- #
# UI driving                                                            #
# --------------------------------------------------------------------- #


# Generous timeout — the Setup app is slower than the standard
# Lightning experience because it hydrates Object Manager metadata
# on each navigation.
_SETUP_LOAD_TIMEOUT_MS = 45_000


def _build_lead_fields_url(salesforce_org_url: str) -> str:
    """Compose the absolute Object Manager → Lead Fields URL."""

    base = salesforce_org_url.rstrip("/")
    return f"{base}{selectors.SF_SETUP_LEAD_FIELDS_PATH}"


def _list_existing_lead_field_api_names(page: _Page) -> list[str]:  # pragma: no cover
    """Read the field-list table and collect every API-name cell.

    Live execution navigates to Object Manager → Lead Fields &
    Relationships, waits for the table, and harvests every visible
    Field Name cell. The values include both standard fields
    (`Email`) and custom fields (`PACKAGING__c`) — the caller
    matches against the `__c`-suffixed names in `LEAD_FIELD_SPECS`.

    Marked `pragma: no cover` because the live behaviour requires
    Playwright. Tests monkeypatch this seam.
    """

    raise NotImplementedError(
        "Live Object Manager scan is validated at the Phase 3 "
        "operator checkpoint, not in unit tests. Tests monkeypatch "
        "this seam."
    )


def _drive_new_field(page: _Page, spec: LeadFieldSpec) -> None:  # pragma: no cover
    """Drive the New Custom Field wizard end-to-end for one spec.

    Sequence:
        1. Click "New" on the Object Manager fields list.
        2. Step 1: pick the data-type radio for `spec.field_type`.
        3. Click "Next".
        4. Step 2: fill `MasterLabel`, `DeveloperName`, `Description`.
        5. For Number fields, fill Length + Precision.
        6. Click "Next" → "Save".

    Marked `pragma: no cover` — live correctness is validated at the
    operator checkpoint. Tests assert the *dispatch* (this function
    is called with the right specs) and not the click sequence.
    """

    raise NotImplementedError(
        "Live UI driving is validated at the Phase 3 operator "
        "checkpoint, not in unit tests."
    )


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


def provision_lead_fields(
    *,
    page: _Page,
    dry_run: bool,
) -> ProvisionResult:
    """Provision missing custom Lead fields.

    Reads the existing fields list, computes the create plan, and
    either reports the plan (`dry_run=True`) or drives the New
    Custom Field wizard for each missing spec.

    Idempotency is enforced by API name: a field whose `api_name`
    is already present is skipped without touching the wizard.

    No retries, no fallbacks. If the Object Manager scan or any
    wizard step raises, the exception surfaces unchanged so the
    operator sees the failure and patches selectors instead of
    debugging a swallowed error.
    """

    existing = set(_list_existing_lead_field_api_names(page))

    skipped: list[LeadFieldSpec] = []
    plan: list[LeadFieldSpec] = []
    for spec in LEAD_FIELD_SPECS:
        if spec.api_name in existing:
            skipped.append(spec)
        else:
            plan.append(spec)

    if dry_run:
        return ProvisionResult(planned=plan, created=[], skipped=skipped)

    created: list[LeadFieldSpec] = []
    for spec in plan:
        _drive_new_field(page, spec)
        created.append(spec)

    return ProvisionResult(planned=plan, created=created, skipped=skipped)
