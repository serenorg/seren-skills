"""Salesforce Lightning Playwright client.

Thin wrapper around an authenticated Playwright Page. In Phase 1 the
only read this client supports is `fetch_first_lead` — the dry-run
target. Phase 2+ adds Lead-list filtering, Lead-detail navigation,
Note write, and dashboard read on top of this same surface.

The whole skill is constrained to Lightning UI automation; this
module is the choke point that enforces it. There is no REST/SOQL/
Apex path here and there must never be — see the SKILL.md privacy
+ compliance contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from scripts.sf import selectors


# --------------------------------------------------------------------- #
# Protocols                                                             #
# --------------------------------------------------------------------- #


class _Locator(Protocol):
    """Subset of `playwright.sync_api.Locator` that this module uses."""

    # `.first` returns a Locator resolved to the first DOM match. We
    # need it because the row+anchor combined selector legitimately
    # matches many elements (multiple rows × multiple record anchors
    # per row) and Playwright's strict mode raises on get_attribute
    # against a multi-match locator.
    @property
    def first(self) -> "_Locator": ...

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
    """One Lead row as scraped from a Lightning list view.

    `record_id` is the 15/18-char Salesforce id pulled out of the
    `href` on the Name link. We surface both so downstream callers
    have a stable handle (id) and a human-readable label (name).
    """

    record_id: str
    name: str
    source_url: str


# --------------------------------------------------------------------- #
# Helpers                                                               #
# --------------------------------------------------------------------- #


# Generous timeout — Lightning list views are infamous for taking
# 5–10s to hydrate even when the rest of the app is responsive.
_LIST_LOAD_TIMEOUT_MS = 30_000


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


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


def fetch_first_lead(
    *,
    page: _Page,
    salesforce_org_url: str,
) -> LeadRow:
    """Navigate to the Lead list and return the first visible row.

    Phase 1 dry-run target. No filtering by `PACKAGING__c` yet — that
    arrives in Phase 3 when the All Sources report exists. This is
    intentionally "the first row that loads", which is enough to
    prove the SSO + Lightning navigation path end-to-end.

    Returns a `LeadRow`. Raises if the list view fails to load, the
    first row never appears, or the row's name link is missing — all
    of those are surfaced to the operator as-is, since each one
    points at a different selector-rotation or auth failure.
    """

    list_url = _build_lead_list_url(salesforce_org_url)
    page.goto(list_url, timeout=_LIST_LOAD_TIMEOUT_MS)

    page.wait_for_selector(
        selectors.SF_LEAD_LIST_FIRST_ROW, timeout=_LIST_LOAD_TIMEOUT_MS
    )

    # The combined selector matches every row × every record-linked
    # anchor in the list, so resolve to the first match in document
    # order. The first DOM anchor that meets the predicate is the
    # Name cell of the first data row — exactly what we want.
    link = page.locator(
        f"{selectors.SF_LEAD_LIST_FIRST_ROW} {selectors.SF_LEAD_ROW_NAME_LINK}"
    ).first

    href = link.get_attribute("href")
    if not href:
        raise RuntimeError(
            "Lead name link missing href — selector "
            f"{selectors.SF_LEAD_ROW_NAME_LINK!r} matched but the href "
            "attribute was empty"
        )

    name = link.inner_text().strip()
    if not name:
        raise RuntimeError(
            "Lead name link rendered without text — selector matched "
            "but the cell is empty"
        )

    return LeadRow(
        record_id=_record_id_from_href(href),
        name=name,
        source_url=list_url,
    )
