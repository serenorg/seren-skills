"""Salesforce Lightning Playwright client.

Thin wrapper around an authenticated Playwright Page. Two surfaces:

* `fetch_first_lead` — list-view read for the dry-run probe.
* `read_project_business_unit` / `populate_is_packaging` — per-Lead
  detail-page read of the `Project Business Unit` field, which is
  the cross-division gate the Phase 4 Note-write path enforces.

The whole skill is constrained to Lightning UI automation; this
module is the choke point that enforces it. There is no REST/SOQL/
Apex path here and there must never be — see the SKILL.md privacy
+ compliance contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional, Protocol

from scripts.sf import selectors

# Playwright's TimeoutError is what `page.wait_for_selector` raises when
# the selector never resolves within the timeout. We catch it by class
# in `read_project_business_unit` so a Lead whose layout omits the PBU
# field altogether returns `None` instead of crashing the cycle. The
# import is wrapped because client.py is also exercised in unit tests
# that mock the Playwright surface — those tests still get a usable
# class to raise via the fallback. Issue #764.
try:
    from playwright.sync_api import (  # type: ignore[import-not-found]
        TimeoutError as _PlaywrightTimeoutError,
    )
except ImportError:  # pragma: no cover — Playwright is a runtime dep
    _PlaywrightTimeoutError = TimeoutError  # type: ignore[assignment]


# Lightning renders `[[…]]` as a placeholder when the visible text of a
# field cannot be resolved — most commonly because the running user lacks
# field-level read access, but also when an upstream import inserted a
# literal placeholder into the field. Either way, the value is not a real
# Lead identifier and downstream enrichment cannot use it. We skip rows
# whose Name link renders this pattern. Verified live on
# herrmannultraschall.lightning.force.com 2026-05-21 against Lead
# 00QS700000L7ELYMA3, which surfaced as `[[Unknown]]`.
_LIGHTNING_NO_VALUE_PATTERN = re.compile(r"^\[\[.*\]\]$")


# --------------------------------------------------------------------- #
# Protocols                                                             #
# --------------------------------------------------------------------- #


class _Locator(Protocol):
    """Subset of `playwright.sync_api.Locator` that this module uses."""

    # `.first` returns a Locator resolved to the first DOM match. Used
    # by `read_project_business_unit` for single-row label/value reads.
    @property
    def first(self) -> "_Locator": ...

    # `.all()` resolves the locator to one Locator per DOM match. The
    # Lead list iteration uses this so we can skip rows whose Name
    # column renders Lightning's `[[…]]` placeholder.
    def all(self) -> list["_Locator"]: ...

    # `.count()` returns the number of DOM matches without raising.
    # `read_project_business_unit` uses this to detect a legitimately
    # empty PBU field (label rendered, value span omitted) — see #759.
    def count(self) -> int: ...

    def get_attribute(self, name: str) -> str | None: ...
    def inner_text(self) -> str: ...


class _Page(Protocol):
    """Subset of `playwright.sync_api.Page` that this module uses."""

    url: str

    def goto(self, url: str, *, timeout: int = ...) -> object: ...
    def wait_for_selector(self, selector: str, *, timeout: int = ...) -> object: ...
    def locator(self, selector: str) -> _Locator: ...


# --------------------------------------------------------------------- #
# Result types                                                          #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class LeadRow:
    """One Lead row.

    `record_id` is the 15/18-char Salesforce id. We surface it plus
    a human-readable name so downstream callers have a stable handle
    and a label.

    `is_packaging` is a tri-state:
      * `None` — not yet read from the Lead detail page (default
        for list-view rows).
      * `True` — Project Business Unit == "PACKAGING".
      * `False` — Project Business Unit was anything else (PL / MD /
        NW / blank).

    The cross-division gate (`enrich_lead.is_packaging_lead`) reads
    this via `getattr` with a False default, so a `None` value (not
    yet read) fails closed — exactly the desired P0 mis-routing
    defense.

    Populate via `populate_is_packaging(page, lead, org_url)` before
    invoking the Phase 4 Note-write path.
    """

    record_id: str
    name: str
    source_url: str
    is_packaging: Optional[bool] = None


# --------------------------------------------------------------------- #
# Helpers                                                               #
# --------------------------------------------------------------------- #


# Generous timeout — Lightning list views are infamous for taking
# 5–10s to hydrate even when the rest of the app is responsive.
_LIST_LOAD_TIMEOUT_MS = 30_000

# Lead detail pages hydrate slightly faster than list views because
# they render a single record, but the Details tab still pulls down
# the highlights/key-fields panel asynchronously.
_DETAIL_LOAD_TIMEOUT_MS = 30_000


def _record_id_from_href(href: str) -> str:
    """Extract the Salesforce record id from a Lightning record URL.

    Lightning Lead URLs appear in two shapes in the wild:
        /lightning/r/Lead/00Q5g00000XYZAbc/view   (legacy / entity-tagged)
        /lightning/r/00Q5g00000XYZAbc/view        (HU 2026-05 live shape)

    Both put the 15- or 18-char Salesforce id immediately after the
    last `/r/` segment, so we anchor on `/r/` and skip an optional
    entity-name segment (e.g. `Lead/`) that some orgs still emit.
    """

    marker = "/r/"
    if marker not in href:
        raise ValueError(f"Lead record id not found in href: {href!r}")
    after = href.rsplit(marker, 1)[1]
    first = after.split("/", 1)[0]
    # Salesforce record ids are 15 or 18 chars and always start
    # with `0`. If the first segment after `/r/` doesn't, it's the
    # legacy entity slug (e.g. `Lead`) and the id is one segment
    # deeper. Anchor on the leading `0` to keep us tolerant to any
    # future slug rotation.
    if not first.startswith("0"):
        rest = after.split("/", 2)
        if len(rest) >= 2:
            return rest[1]
    return first


def _build_lead_list_url(salesforce_org_url: str) -> str:
    """Compose the absolute Lead list URL.

    Strips any trailing slash on the org URL so we never produce a
    double-slash that some Lightning routers reject.
    """

    base = salesforce_org_url.rstrip("/")
    return f"{base}{selectors.SF_LEAD_LIST_PATH}"


def _build_lead_detail_url(salesforce_org_url: str, record_id: str) -> str:
    """Compose the absolute Lead detail URL for one record id."""

    base = salesforce_org_url.rstrip("/")
    path = selectors.SF_LEAD_DETAIL_PATH_TEMPLATE.format(record_id=record_id)
    return f"{base}{path}"


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


def fetch_open_leads(
    *,
    page: _Page,
    salesforce_org_url: str,
    limit: int,
) -> list[LeadRow]:
    """Navigate to the Lead list and return up to `limit` visible rows.

    Skips Lightning's `[[…]]` placeholder rows the same way
    `fetch_first_lead` does. Each `LeadRow.is_packaging` is None — the
    list view does not surface the Project Business Unit column, so
    callers must invoke `populate_is_packaging` per Lead before any
    live-write decision.

    Raises `RuntimeError` with diagnostic counters when zero readable
    rows are visible — this matches the single-lead path's failure mode
    so an operator hitting either flow gets the same FLS / placeholder
    advice.
    """

    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")

    list_url = _build_lead_list_url(salesforce_org_url)
    page.goto(list_url, timeout=_LIST_LOAD_TIMEOUT_MS)

    page.wait_for_selector(
        selectors.SF_LEAD_LIST_FIRST_ROW, timeout=_LIST_LOAD_TIMEOUT_MS
    )

    links = page.locator(
        f"{selectors.SF_LEAD_LIST_FIRST_ROW} {selectors.SF_LEAD_ROW_NAME_LINK}"
    ).all()

    rows: list[LeadRow] = []
    placeholder_rows = 0
    missing_href = 0
    blank_text = 0
    non_lead_rows = 0
    for link in links:
        if len(rows) >= limit:
            break
        href = link.get_attribute("href")
        if not href:
            missing_href += 1
            continue
        name = link.inner_text().strip()
        if not name:
            blank_text += 1
            continue
        if _LIGHTNING_NO_VALUE_PATTERN.match(name):
            placeholder_rows += 1
            continue
        record_id = _record_id_from_href(href)
        # The combined `LIST_FIRST_ROW + ROW_NAME_LINK` selector also
        # matches the Lead Owner avatar anchor (a User reference, prefix
        # `005`). Filter to the Lead ObjectPrefix `00Q` — a documented
        # Salesforce invariant — so batch mode doesn't pipe User
        # profiles into the Perplexity / Claude pipeline. Issue #774.
        if not record_id.startswith("00Q"):
            non_lead_rows += 1
            continue
        rows.append(
            LeadRow(
                record_id=record_id,
                name=name,
                source_url=list_url,
            )
        )

    if not rows:
        raise RuntimeError(
            "No Lead row with a readable name found in the list view "
            f"(rows scanned={len(links)}, "
            f"missing href={missing_href}, "
            f"blank text={blank_text}, "
            f"Lightning placeholder `[[…]]`={placeholder_rows}, "
            f"non-Lead record (`005`/etc.)={non_lead_rows}). "
            "If placeholder count is non-zero, the running SSO user likely "
            "lacks field-level read access to Lead.Name or the source data "
            "contains placeholder values — escalate to the Salesforce admin."
        )
    return rows


def fetch_first_lead(
    *,
    page: _Page,
    salesforce_org_url: str,
) -> LeadRow:
    """Navigate to the Lead list and return the first visible row.

    Thin wrapper over `fetch_open_leads` for the single-lead dry-run
    path. The combined-list DOM walk and placeholder-skipping live in
    `fetch_open_leads` so batch and single-lead flows can't drift.
    """

    return fetch_open_leads(
        page=page, salesforce_org_url=salesforce_org_url, limit=1
    )[0]


def read_project_business_unit(
    *,
    page: _Page,
    record_id: str,
    salesforce_org_url: str,
) -> Optional[str]:
    """Navigate to the Lead detail page and read the field value.

    Returns the inner text of the Project Business Unit value cell,
    stripped. Returns `None` when the field is present on the page
    but its value cell is empty (a Lead that has not yet been
    classified) — the caller treats `None` as "not PK".

    Raises `RuntimeError` when the detail page never renders the
    field at all — that points at either a permissions issue or a
    selector rotation, both of which need operator attention rather
    than silent fail-closed behavior.
    """

    detail_url = _build_lead_detail_url(salesforce_org_url, record_id)
    page.goto(detail_url, timeout=_DETAIL_LOAD_TIMEOUT_MS)

    # Wait on the FIELD WRAPPER, not the value span. Lightning renders
    # the wrapper + label whenever the field is on the layout, even for
    # Leads whose PBU is unset; the value span is only emitted when a
    # value exists. Waiting on the value span timed out for 30s on
    # legitimately empty fields (legal-services Lead, etc.) and crashed
    # the `--allow-live` cycle — see #759.
    #
    # HU runs multiple Lead Record Types with different page layouts —
    # the PBU field is only on the PK-routed layouts. For Leads on a
    # different layout the wrapper itself never renders, so the wait
    # times out. Treat that as "PBU not on this layout" → return `None`
    # (caller's cross-division gate then fails closed). Issue #764.
    try:
        page.wait_for_selector(
            selectors.SF_LEAD_DETAIL_PROJECT_BUSINESS_UNIT_FIELD,
            timeout=_DETAIL_LOAD_TIMEOUT_MS,
        )
    except _PlaywrightTimeoutError:
        return None

    value_locator = page.locator(
        selectors.SF_LEAD_DETAIL_PROJECT_BUSINESS_UNIT_VALUE
    )
    if value_locator.count() == 0:
        # Wrapper rendered but value span absent → field is on the
        # layout, just unset. Same fail-closed outcome as a missing
        # wrapper — caller treats `None` as "not PK".
        return None

    text = value_locator.first.inner_text().strip()
    return text or None


def populate_is_packaging(
    *,
    page: _Page,
    lead: LeadRow,
    salesforce_org_url: str,
) -> LeadRow:
    """Return a copy of `lead` with `is_packaging` set from the DOM.

    Drives the Lead detail page once, reads Project Business Unit,
    and returns a new LeadRow with `is_packaging` set to
    `True` iff the value matches `SF_PROJECT_BUSINESS_UNIT_PK_VALUE`
    exactly. Any other value (including `None`/blank) sets
    `is_packaging=False`.

    Idempotent — calling twice produces the same result; the
    Salesforce session is the only side effect.
    """

    value = read_project_business_unit(
        page=page,
        record_id=lead.record_id,
        salesforce_org_url=salesforce_org_url,
    )
    is_pk = value == selectors.SF_PROJECT_BUSINESS_UNIT_PK_VALUE
    return replace(lead, is_packaging=is_pk)
