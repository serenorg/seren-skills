"""Salesforce Lightning Playwright client.

Thin wrapper around an authenticated Playwright Page. Two surfaces:

* `fetch_first_lead` — list-view read for the dry-run probe.
* `fetch_all_sources_pk_leads` — pinned PK report read for batch runs.
* `read_business_unit_packaging_checked` / `populate_is_packaging` —
  per-record detail-page read of the Business Unit -> PACKAGING
  checkbox, which is the cross-division gate the Phase 4 Note-write
  path enforces.

The whole skill is constrained to Lightning UI automation; this
module is the choke point that enforces it. There is no REST/SOQL/
Apex path here and there must never be — see the SKILL.md privacy
+ compliance contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Optional, Protocol

from scripts.sf import build_all_sources_leads_report as all_leads_report
from scripts.sf import selectors

# Playwright's TimeoutError is what `page.wait_for_selector` raises when
# the selector never resolves within the timeout. We catch it by class
# in `read_business_unit_packaging_checked` so a record whose layout
# omits the Business Unit section returns `None` instead of crashing. The
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


class ZeroLeadsFoundError(RuntimeError):
    """fetch_open_leads found zero readable Lead rows in the list view.

    Subclasses RuntimeError so existing single-lead callers
    (``fetch_first_lead``) keep their current "loud traceback" behavior —
    a traceback in single-lead mode is acceptable because the operator
    is in front of the terminal driving the cycle directly. Batch
    callers in ``agent.py`` catch this class specifically at the
    dispatch site and render an operator-readable summary so the
    cron-parseable summary contract is preserved on FLS-gap / empty-list
    runs. Issue #776.
    """


# --------------------------------------------------------------------- #
# Protocols                                                             #
# --------------------------------------------------------------------- #


class _Locator(Protocol):
    """Subset of `playwright.sync_api.Locator` that this module uses."""

    # `.first` returns a Locator resolved to the first DOM match. Used
    # by the Business Unit checkbox reader for single-field reads.
    @property
    def first(self) -> "_Locator": ...

    # `.all()` resolves the locator to one Locator per DOM match. The
    # Lead list iteration uses this so we can skip rows whose Name
    # column renders Lightning's `[[…]]` placeholder.
    def all(self) -> list["_Locator"]: ...

    # `.count()` returns the number of DOM matches without raising.
    # `read_business_unit_packaging_checked` uses this to distinguish
    # checked and unchecked boolean renderers.
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
      * `True` — Business Unit -> PACKAGING is checked.
      * `False` — PACKAGING is unchecked or the section cannot be read.

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

# Some lower Salesforce record-layout sections do not materialize their
# field rows until the section is scrolled into view. Probe briefly, then
# scroll the Business Unit header and retry with the full detail budget.
_DETAIL_INITIAL_FIELD_PROBE_TIMEOUT_MS = 5_000
_DETAIL_SECTION_SCROLL_TIMEOUT_MS = 5_000

# Report viewer hydrates through Lightning/Aura and is slower than the
# stock list view. Match the pinned-artifact validator's timeout.
_REPORT_LOAD_TIMEOUT_MS = 45_000

# The top-level report page usually has no row links; the data lives in
# a Lightning report iframe. Probe the top page briefly, then spend the
# real wait budget on frame scopes.
_REPORT_TOP_PAGE_PROBE_TIMEOUT_MS = 1_000


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


def _record_object_name(record_id: str) -> str:
    """Return the Lightning object API name for supported record ids."""

    if record_id.startswith("003"):
        return "Contact"
    return "Lead"


def _build_record_detail_url(salesforce_org_url: str, record_id: str) -> str:
    """Compose the absolute Lightning detail URL for one record id."""

    base = salesforce_org_url.rstrip("/")
    path = selectors.SF_RECORD_DETAIL_PATH_TEMPLATE.format(
        object_name=_record_object_name(record_id),
        record_id=record_id,
    )
    return f"{base}{path}"


def _scoped_selector(
    parent_selectors: tuple[str, ...], child_selectors: tuple[str, ...]
) -> str:
    """Build a comma-joined selector that scopes every child to a parent."""

    return ", ".join(
        f"{parent} {child}"
        for parent in parent_selectors
        for child in child_selectors
    )


def _business_unit_packaging_checked_selector() -> str:
    """Selector for checked-state markers inside the PACKAGING field."""

    return _scoped_selector(
        selectors.SF_RECORD_DETAIL_BUSINESS_UNIT_PACKAGING_FIELD_SELECTORS,
        selectors.SF_RECORD_DETAIL_BOOLEAN_TRUE_MARKERS,
    )


def _scroll_business_unit_section_into_view(page: _Page) -> None:
    """Best-effort scroll for lazily rendered lower detail sections."""

    try:
        section = page.locator(
            selectors.SF_RECORD_DETAIL_BUSINESS_UNIT_SECTION_LABEL
        ).first
        scroll = getattr(section, "scroll_into_view_if_needed")
        scroll(timeout=_DETAIL_SECTION_SCROLL_TIMEOUT_MS)
    except Exception:  # noqa: BLE001
        # Absence still fails closed when the PACKAGING field wait below
        # times out. This helper only nudges virtualized sections to
        # render; it must not turn a missing section into a hard crash.
        return


def _lead_rows_from_record_links(
    *,
    links: list[_Locator],
    source_url: str,
    limit: int,
    source_label: str,
) -> list[LeadRow]:
    """Extract readable Lead rows from Lightning record anchors."""

    rows: list[LeadRow] = []
    seen_record_ids: set[str] = set()
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
        # The same broad anchor selector can match Owner/User links.
        # Filter to Lead ObjectPrefix `00Q` before downstream enrichment.
        if not record_id.startswith("00Q"):
            non_lead_rows += 1
            continue
        if record_id in seen_record_ids:
            continue
        seen_record_ids.add(record_id)
        rows.append(
            LeadRow(
                record_id=record_id,
                name=name,
                source_url=source_url,
            )
        )

    if not rows:
        raise ZeroLeadsFoundError(
            f"No Lead row with a readable name found in the {source_label} "
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


def _collect_report_record_links(page: _Page) -> list[_Locator]:
    """Collect report record anchors from the page and report frames."""

    try:
        page.wait_for_selector(
            selectors.SF_REPORT_VIEWER_IFRAME,
            timeout=_REPORT_LOAD_TIMEOUT_MS,
        )
    except _PlaywrightTimeoutError:
        # Some orgs may render report rows directly in the top page.
        # Keep going and probe whatever scopes are available.
        pass

    scopes = [page, *list(getattr(page, "frames", []))]
    links: list[_Locator] = []
    for idx, scope in enumerate(scopes):
        timeout = (
            _REPORT_TOP_PAGE_PROBE_TIMEOUT_MS
            if idx == 0
            else _REPORT_LOAD_TIMEOUT_MS
        )
        try:
            scope.wait_for_selector(
                selectors.SF_REPORT_RECORD_LINK,
                timeout=timeout,
            )
        except _PlaywrightTimeoutError:
            continue
        links.extend(scope.locator(selectors.SF_REPORT_RECORD_LINK).all())
    return links


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
    list view does not expose the Business Unit checkbox state, so
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

    return _lead_rows_from_record_links(
        links=links,
        source_url=list_url,
        limit=limit,
        source_label="list view",
    )


def fetch_all_sources_pk_leads(
    *,
    page: _Page,
    limit: int,
) -> list[LeadRow]:
    """Read up to `limit` Leads from the pinned All Sources PK Leads report.

    The report is operator-owned and acts as a candidate source. Live
    batch still performs the Business Unit -> PACKAGING detail-page
    gate before writing Notes; this source just prevents the cron from
    spending research/model budget on the generic AllOpenLeads list
    view. Issue #838.
    """

    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")

    report_url = all_leads_report.PINNED_REPORT_URL
    page.goto(report_url, timeout=_REPORT_LOAD_TIMEOUT_MS)
    links = _collect_report_record_links(page)
    if not links:
        raise ZeroLeadsFoundError(
            "No Lead row with a readable name found in the "
            "All Sources PK Leads report (rows scanned=0, "
            "missing href=0, blank text=0, "
            "Lightning placeholder `[[…]]`=0, "
            "non-Lead record (`005`/etc.)=0). "
            "If this report should contain PK Leads, confirm the pinned "
            "report still loads and exposes Lead Name links to the running "
            "Salesforce user."
        )

    return _lead_rows_from_record_links(
        links=links,
        source_url=report_url,
        limit=limit,
        source_label="All Sources PK Leads report",
    )


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


def read_business_unit_packaging_checked(
    *,
    page: _Page,
    record_id: str,
    salesforce_org_url: str,
) -> Optional[bool]:
    """Return the Business Unit -> PACKAGING checkbox state.

    Returns:
      * `True` when the PACKAGING row/field is present and checked.
      * `False` when the row/field is present and readable but unchecked.
      * `None` when the Business Unit section or PACKAGING field cannot
        be read. Callers convert this to the same fail-closed behavior
        as unchecked.
    """

    detail_url = _build_record_detail_url(salesforce_org_url, record_id)
    page.goto(detail_url, timeout=_DETAIL_LOAD_TIMEOUT_MS)

    try:
        page.wait_for_selector(
            selectors.SF_RECORD_DETAIL_BUSINESS_UNIT_PACKAGING_FIELD,
            timeout=_DETAIL_INITIAL_FIELD_PROBE_TIMEOUT_MS,
        )
    except _PlaywrightTimeoutError:
        _scroll_business_unit_section_into_view(page)
        try:
            page.wait_for_selector(
                selectors.SF_RECORD_DETAIL_BUSINESS_UNIT_PACKAGING_FIELD,
                timeout=_DETAIL_LOAD_TIMEOUT_MS,
            )
        except _PlaywrightTimeoutError:
            return None

    checked_locator = page.locator(_business_unit_packaging_checked_selector())
    if checked_locator.count() > 0:
        return True

    field_text = page.locator(
        selectors.SF_RECORD_DETAIL_BUSINESS_UNIT_PACKAGING_FIELD
    ).first.inner_text()
    normalized_lines = {
        line.strip().lower() for line in field_text.splitlines() if line.strip()
    }
    return "true" in normalized_lines or any(
        mark in field_text for mark in ("✓", "✔", "☑")
    )


def populate_is_packaging(
    *,
    page: _Page,
    lead: LeadRow,
    salesforce_org_url: str,
) -> LeadRow:
    """Return a copy of `lead` with `is_packaging` set from the DOM.

    Drives the record detail page once, reads Business Unit ->
    PACKAGING, and returns a new LeadRow with `is_packaging=True`
    iff the checkbox is checked. Unchecked, missing, or unreadable
    state sets `is_packaging=False`.

    Idempotent — calling twice produces the same result; the
    Salesforce session is the only side effect.
    """

    checked = read_business_unit_packaging_checked(
        page=page,
        record_id=lead.record_id,
        salesforce_org_url=salesforce_org_url,
    )
    return replace(lead, is_packaging=checked is True)
