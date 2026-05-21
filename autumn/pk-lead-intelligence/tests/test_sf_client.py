"""Unit tests for scripts/sf/client.py.

The Salesforce client is a thin Playwright wrapper. The tests here
exercise the pure helpers (record-id extraction, URL building) and
the orchestration of `fetch_first_lead` against fakes — they do not
exercise Lightning itself. That happens at the Phase 1 dry-run
checkpoint with the operator watching.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from scripts.sf import client as sf_client
from scripts.sf import selectors


# --------------------------------------------------------------------- #
# Pure-helper tests                                                     #
# --------------------------------------------------------------------- #


def test_record_id_from_href_extracts_15char_id_legacy_shape():
    """Legacy `/r/Lead/<id>/view` shape from older orgs."""

    href = "/lightning/r/Lead/00Q5g00000XYZAbc/view"
    assert sf_client._record_id_from_href(href) == "00Q5g00000XYZAbc"


def test_record_id_from_href_extracts_18char_id_legacy_shape():
    """Salesforce returns 15- or 18-char record ids depending on
    serialization; both must parse identically."""

    href = "/lightning/r/Lead/00Q5g00000XYZAbcDEF/view"
    assert sf_client._record_id_from_href(href) == "00Q5g00000XYZAbcDEF"


def test_record_id_from_href_extracts_id_modern_shape():
    """HU's 2026-05 Lightning emits `/r/<id>/view` with no entity slug."""

    href = "/lightning/r/00QS700000Gz68yMAB/view"
    assert sf_client._record_id_from_href(href) == "00QS700000Gz68yMAB"


def test_record_id_from_href_skips_arbitrary_entity_slug():
    """Any entity slug — not just `Lead` — is skipped to find the id."""

    href = "/lightning/r/Account/001S700000XYZ/view"
    assert sf_client._record_id_from_href(href) == "001S700000XYZ"


def test_record_id_from_href_raises_when_marker_absent():
    """`/r/` is the load-bearing marker. Without it we cannot locate
    the id at all (e.g. a list-view URL slipped in by mistake)."""

    with pytest.raises(ValueError, match="Lead record id not found"):
        sf_client._record_id_from_href("/lightning/o/Lead/list")


def test_build_lead_list_url_strips_trailing_slash():
    """A trailing slash on the org URL must not produce `//lightning/...`."""

    assert (
        sf_client._build_lead_list_url("https://acme.lightning.force.com/")
        == f"https://acme.lightning.force.com{selectors.SF_LEAD_LIST_PATH}"
    )


def test_build_lead_list_url_handles_no_trailing_slash():
    assert (
        sf_client._build_lead_list_url("https://acme.lightning.force.com")
        == f"https://acme.lightning.force.com{selectors.SF_LEAD_LIST_PATH}"
    )


# --------------------------------------------------------------------- #
# fetch_first_lead with fakes                                           #
# --------------------------------------------------------------------- #


@dataclass
class FakeLocator:
    href: str | None = None
    text: str = ""
    match_count: int = 1

    @property
    def first(self):
        # `read_project_business_unit` uses `.first` for single-cell
        # reads. Single-row tests inherit this identity behavior.
        return self

    def all(self):
        # `fetch_first_lead` iterates over `.all()`. The single-row
        # tests model a list of one resolved match.
        return [self]

    def count(self):
        # `read_project_business_unit` calls `.count()` on the value
        # span locator to detect a legitimately empty PBU field (#759).
        # Default is 1 so existing fixtures keep behaving like a
        # single-match locator without tracking match counts.
        return self.match_count

    def get_attribute(self, name):
        assert name == "href"
        return self.href

    def inner_text(self):
        return self.text


@dataclass
class FakeRowList:
    """Fake for a locator that resolves to multiple matches.

    `fetch_first_lead` calls `.all()` to iterate over every row+anchor
    pair in the Lead list. This fake returns a pre-built list of
    `FakeLocator`s, one per simulated DOM row, so tests can model the
    multi-row scan (e.g. a placeholder row followed by a real row).
    """

    rows: list[FakeLocator]

    def all(self):
        return list(self.rows)


@dataclass
class FakePage:
    locator_for: dict[str, FakeLocator] = field(default_factory=dict)
    call_log: list[tuple[str, tuple]] = field(default_factory=list)
    url: str = ""
    # Per-selector exception map: when `wait_for_selector(sel)` is
    # called and `sel` is a key here, raise the stored exception.
    # Lets tests model Playwright's `TimeoutError` for selectors that
    # would never resolve in production (PBU field absent from a Lead's
    # page layout — issue #764).
    wait_for_selector_raises_for: dict[str, BaseException] = field(
        default_factory=dict
    )

    def goto(self, url, *, timeout: int = 0):
        self.call_log.append(("goto", (url,)))
        self.url = url

    def wait_for_selector(self, selector, *, timeout: int = 0):
        self.call_log.append(("wait_for_selector", (selector,)))
        if selector in self.wait_for_selector_raises_for:
            raise self.wait_for_selector_raises_for[selector]

    def locator(self, selector):
        self.call_log.append(("locator", (selector,)))
        return self.locator_for[selector]


def _make_page_with_link(*, href: str | None, text: str) -> FakePage:
    """Build a FakePage whose first-row-name-link locator returns a row."""

    sel = (
        f"{selectors.SF_LEAD_LIST_FIRST_ROW} "
        f"{selectors.SF_LEAD_ROW_NAME_LINK}"
    )
    return FakePage(locator_for={sel: FakeLocator(href=href, text=text)})


def _make_page_with_rows(rows: list[tuple[str | None, str]]) -> FakePage:
    """Build a FakePage whose name-link locator resolves to many rows."""

    sel = (
        f"{selectors.SF_LEAD_LIST_FIRST_ROW} "
        f"{selectors.SF_LEAD_ROW_NAME_LINK}"
    )
    return FakePage(
        locator_for={
            sel: FakeRowList(
                rows=[FakeLocator(href=h, text=t) for h, t in rows]
            )
        }
    )


def test_fetch_first_lead_returns_parsed_row():
    """Happy path. Goes to the list, waits for the row, returns it."""

    page = _make_page_with_link(
        href="/lightning/r/Lead/00Q5g00000XYZAbc/view",
        text="Acme GmbH",
    )

    row = sf_client.fetch_first_lead(
        page=page,
        salesforce_org_url="https://acme.lightning.force.com",
    )

    assert row.record_id == "00Q5g00000XYZAbc"
    assert row.name == "Acme GmbH"
    assert (
        row.source_url
        == f"https://acme.lightning.force.com{selectors.SF_LEAD_LIST_PATH}"
    )

    # goto → wait_for_selector → locator. Order matters.
    steps = [c[0] for c in page.call_log]
    assert steps == ["goto", "wait_for_selector", "locator"]


def test_fetch_first_lead_strips_whitespace_in_name():
    """Lightning frequently pads cells with leading/trailing whitespace."""

    page = _make_page_with_link(
        href="/lightning/r/Lead/00Q5g00000XYZAbc/view",
        text="   Acme GmbH   \n",
    )

    row = sf_client.fetch_first_lead(
        page=page,
        salesforce_org_url="https://acme.lightning.force.com",
    )
    assert row.name == "Acme GmbH"


def test_fetch_first_lead_skips_lightning_placeholder_rows_and_returns_real_lead():
    """A row whose Name link renders Lightning's `[[…]]` placeholder is
    skipped and the next row's real Lead is returned.

    Regression for issue #755: HU's first Lead list row surfaced
    `[[Unknown]]` (Lightning's field-level-access placeholder), which
    produced `00QS700000L7ELYMA3___Unknown__.docx` outputs. The fix
    iterates rows and skips placeholders instead of silently using one.
    """

    page = _make_page_with_rows(
        [
            ("/lightning/r/Lead/00QS700000L7ELYMA3/view", "[[Unknown]]"),
            ("/lightning/r/Lead/00Q5g00000XYZAbc/view", "Acme GmbH"),
        ]
    )

    row = sf_client.fetch_first_lead(
        page=page,
        salesforce_org_url="https://acme.lightning.force.com",
    )

    assert row.record_id == "00Q5g00000XYZAbc"
    assert row.name == "Acme GmbH"


def test_fetch_first_lead_raises_with_diagnostic_counts_when_no_valid_row():
    """When every row is unusable, the raise message surfaces per-failure
    counts (missing href, blank text, Lightning placeholder) so the
    operator can tell whether they're hit by a permissions gap, a
    selector rotation, or a corrupted import."""

    page = _make_page_with_rows(
        [
            (None, "Acme GmbH"),
            ("/lightning/r/Lead/00Q5g00000XYZAbc/view", "   "),
            ("/lightning/r/Lead/00QS700000L7ELYMA3/view", "[[Unknown]]"),
        ]
    )

    with pytest.raises(RuntimeError) as excinfo:
        sf_client.fetch_first_lead(
            page=page,
            salesforce_org_url="https://acme.lightning.force.com",
        )

    msg = str(excinfo.value)
    assert "missing href=1" in msg
    assert "blank text=1" in msg
    assert "Lightning placeholder `[[…]]`=1" in msg
    assert "field-level read access" in msg


# --------------------------------------------------------------------- #
# fetch_open_leads non-Lead filter (issue #774)                          #
# --------------------------------------------------------------------- #


def test_fetch_open_leads_filters_non_lead_record_ids():
    """User-linked anchors (`005…`) must not surface as Leads.

    The combined `LIST_FIRST_ROW + ROW_NAME_LINK` selector matches every
    record-linked anchor in each row, including the Lead Owner avatar
    (a User reference, prefix `005`). Pre-PR #773 the single-lead path
    always returned the first match — the Lead.Name anchor — so the bug
    was latent. Batch iteration walks past row 1 and exposes it.

    `00Q` is the stable Salesforce ObjectPrefix for the Lead object —
    documented invariant across orgs, sandboxes, and releases.
    """

    page = _make_page_with_rows(
        [
            ("/lightning/r/Lead/00Q5g00000XYZAbc/view", "Acme GmbH"),
            ("/lightning/r/005S700000ClEZ3IAN/view", "ASp"),
            ("/lightning/r/Lead/00Q5g00000ABCdef/view", "Beta Corp"),
            ("/lightning/r/005S700000ClEWzIAN/view", "DWr"),
        ]
    )

    rows = sf_client.fetch_open_leads(
        page=page,
        salesforce_org_url="https://acme.lightning.force.com",
        limit=10,
    )

    assert [r.record_id for r in rows] == ["00Q5g00000XYZAbc", "00Q5g00000ABCdef"]


# --------------------------------------------------------------------- #
# read_project_business_unit                                            #
# --------------------------------------------------------------------- #


def test_read_project_business_unit_returns_none_when_value_span_absent():
    """When the Lead's Project Business Unit is unset, Lightning renders
    the field WRAPPER + label but omits the inner value span. The
    function must wait on the wrapper (always present) and treat a
    missing value span as `None` — not raise a 30s Playwright timeout
    against the value selector that never resolves. Regression for
    issue #759, surfaced by the Daniel Valdez legal-services Lead.
    """

    field_sel = selectors.SF_LEAD_DETAIL_PROJECT_BUSINESS_UNIT_FIELD
    value_sel = selectors.SF_LEAD_DETAIL_PROJECT_BUSINESS_UNIT_VALUE
    page = FakePage(
        locator_for={value_sel: FakeLocator(match_count=0)},
    )

    result = sf_client.read_project_business_unit(
        page=page,
        record_id="00Q5g00000XYZAbc",
        salesforce_org_url="https://acme.lightning.force.com",
    )

    assert result is None
    waits = [c[1][0] for c in page.call_log if c[0] == "wait_for_selector"]
    assert field_sel in waits, (
        "wait_for_selector must anchor on the field wrapper, not the value"
    )
    assert value_sel not in waits, (
        "waiting on the value span is the bug — must not regress"
    )


def test_read_project_business_unit_returns_none_when_pbu_field_not_on_layout():
    """When the Lead's page layout omits the PBU field entirely (a
    non-PK Record Type, e.g. HU's legal-services Lead Record Type),
    the field WRAPPER never renders either. The wait on the wrapper
    times out — Playwright raises TimeoutError. The function must
    catch that and return `None` so the caller's cross-division gate
    fails closed instead of crashing the entire `--allow-live` cycle.

    Regression for issue #764, surfaced by the Daniel Valdez Lead
    after #759 / #763 unblocked SSO and Lead-list navigation.
    """

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    field_sel = selectors.SF_LEAD_DETAIL_PROJECT_BUSINESS_UNIT_FIELD
    page = FakePage(
        wait_for_selector_raises_for={
            field_sel: PlaywrightTimeoutError(
                "Page.wait_for_selector: Timeout 30000ms exceeded."
            ),
        },
    )

    result = sf_client.read_project_business_unit(
        page=page,
        record_id="00Q5g00000XYZAbc",
        salesforce_org_url="https://acme.lightning.force.com",
    )

    assert result is None
    # The wrapper wait happened (and timed out) but the function did
    # NOT proceed to read the value locator — wrapper missing means
    # field absent, no value to read.
    waits = [c[1][0] for c in page.call_log if c[0] == "wait_for_selector"]
    assert waits == [field_sel]
    assert not any(c[0] == "locator" for c in page.call_log)
