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
    scroll_calls: int = 0

    @property
    def first(self):
        # Single-field reads inherit this identity behavior.
        return self

    def all(self):
        # `fetch_first_lead` iterates over `.all()`. The single-row
        # tests model a list of one resolved match.
        return [self]

    def count(self):
        # Checkbox-marker reads call `.count()` to distinguish checked
        # from unchecked fields. Default is 1 so existing fixtures keep
        # behaving like a single-match locator without tracking counts.
        return self.match_count

    def get_attribute(self, name):
        assert name == "href"
        return self.href

    def inner_text(self):
        return self.text

    def scroll_into_view_if_needed(self, *, timeout: int = 0):
        self.scroll_calls += 1


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
    frames: list["FakePage"] = field(default_factory=list)
    # Per-selector exception map: when `wait_for_selector(sel)` is
    # called and `sel` is a key here, raise the stored exception.
    # Lets tests model Playwright's `TimeoutError` for selectors that
    # would never resolve in production (Business Unit section absent).
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


def test_fetch_all_sources_pk_leads_uses_pinned_report_and_filters_lead_links():
    """The PK batch source is the pinned report, not AllOpenLeads."""

    from scripts.sf import build_all_sources_leads_report as all_leads_report

    page = FakePage(
        locator_for={
            selectors.SF_REPORT_RECORD_LINK: FakeRowList(
                rows=[
                    (FakeLocator(
                        href="/lightning/r/Lead/00Q5g00000XYZAbc/view",
                        text="PK Lead",
                    )),
                    (FakeLocator(
                        href="/lightning/r/005S700000ClEZ3IAN/view",
                        text="Lead Owner",
                    )),
                    (FakeLocator(
                        href="/lightning/r/Lead/00Q5g00000ABCdef/view",
                        text="Second PK Lead",
                    )),
                ]
            )
        }
    )

    rows = sf_client.fetch_all_sources_pk_leads(page=page, limit=10)

    assert page.call_log[0] == ("goto", (all_leads_report.PINNED_REPORT_URL,))
    assert [r.record_id for r in rows] == ["00Q5g00000XYZAbc", "00Q5g00000ABCdef"]
    assert all(r.source_url == all_leads_report.PINNED_REPORT_URL for r in rows)


def test_fetch_all_sources_pk_leads_reads_report_iframe_links():
    """Lightning report rows render inside the report app iframe."""

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from scripts.sf import build_all_sources_leads_report as all_leads_report

    frame = FakePage(
        locator_for={
            selectors.SF_REPORT_RECORD_LINK: FakeRowList(
                rows=[
                    FakeLocator(
                        href="/lightning/r/Lead/00Q5g00000XYZAbc/view",
                        text="PK Lead",
                    )
                ]
            )
        },
        url=(
            "https://acme.lightning.force.com/reports/"
            "lightningReportApp.app?reportId=00OS700000IzEBlMAN"
        ),
    )
    page = FakePage(
        locator_for={
            selectors.SF_REPORT_RECORD_LINK: FakeRowList(rows=[]),
        },
        frames=[frame],
        wait_for_selector_raises_for={
            selectors.SF_REPORT_RECORD_LINK: PlaywrightTimeoutError(
                "Page.wait_for_selector: Timeout 45000ms exceeded."
            )
        },
    )

    rows = sf_client.fetch_all_sources_pk_leads(page=page, limit=10)

    assert page.call_log[0] == ("goto", (all_leads_report.PINNED_REPORT_URL,))
    assert [r.record_id for r in rows] == ["00Q5g00000XYZAbc"]


# --------------------------------------------------------------------- #
# Business Unit PK gate                                                 #
# --------------------------------------------------------------------- #


def test_populate_is_packaging_passes_when_lead_business_unit_packaging_checked():
    """Issue #841: PK eligibility comes from Business Unit -> PACKAGING."""

    field_sel = selectors.SF_RECORD_DETAIL_BUSINESS_UNIT_PACKAGING_FIELD
    checked_sel = sf_client._business_unit_packaging_checked_selector()
    page = FakePage(
        locator_for={
            field_sel: FakeLocator(text="PACKAGING"),
            checked_sel: FakeLocator(match_count=1),
        },
    )
    lead = sf_client.LeadRow(
        record_id="00Q5g00000XYZAbc",
        name="PK Lead",
        source_url="https://acme.lightning.force.com/report",
    )

    result = sf_client.populate_is_packaging(
        page=page,
        lead=lead,
        salesforce_org_url="https://acme.lightning.force.com",
    )

    assert result.is_packaging is True
    assert page.call_log[0][0] == "goto"
    assert page.call_log[0][1][0] == (
        "https://acme.lightning.force.com/lightning/r/Lead/"
        "00Q5g00000XYZAbc/view"
    )


def test_populate_is_packaging_fails_when_lead_business_unit_packaging_unchecked():
    """A readable but unchecked PACKAGING field must fail the PK gate."""

    field_sel = selectors.SF_RECORD_DETAIL_BUSINESS_UNIT_PACKAGING_FIELD
    checked_sel = sf_client._business_unit_packaging_checked_selector()
    page = FakePage(
        locator_for={
            field_sel: FakeLocator(text="PACKAGING"),
            checked_sel: FakeLocator(match_count=0),
        },
    )
    lead = sf_client.LeadRow(
        record_id="00Q5g00000XYZAbc",
        name="Non PK Lead",
        source_url="https://acme.lightning.force.com/report",
    )

    result = sf_client.populate_is_packaging(
        page=page,
        lead=lead,
        salesforce_org_url="https://acme.lightning.force.com",
    )

    assert result.is_packaging is False


def test_read_business_unit_packaging_checked_supports_contact_detail_page():
    """Converted Lead redirects can land on Contact detail with same shape."""

    field_sel = selectors.SF_RECORD_DETAIL_BUSINESS_UNIT_PACKAGING_FIELD
    checked_sel = sf_client._business_unit_packaging_checked_selector()
    page = FakePage(
        locator_for={
            field_sel: FakeLocator(text="PACKAGING"),
            checked_sel: FakeLocator(match_count=1),
        },
    )

    result = sf_client.read_business_unit_packaging_checked(
        page=page,
        record_id="003S700000Qmss6IAB",
        salesforce_org_url="https://acme.lightning.force.com",
    )

    assert result is True
    assert page.call_log[0][0] == "goto"
    assert page.call_log[0][1][0] == (
        "https://acme.lightning.force.com/lightning/r/Contact/"
        "003S700000Qmss6IAB/view"
    )


def test_populate_is_packaging_scrolls_lazy_business_unit_section():
    """Lower Lightning sections may not render fields until scrolled."""

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    field_sel = selectors.SF_RECORD_DETAIL_BUSINESS_UNIT_PACKAGING_FIELD
    checked_sel = sf_client._business_unit_packaging_checked_selector()
    section_sel = selectors.SF_RECORD_DETAIL_BUSINESS_UNIT_SECTION_LABEL
    section_locator = FakeLocator(text="Business Unit")

    @dataclass
    class LazyBusinessUnitPage(FakePage):
        field_waits: int = 0

        def wait_for_selector(self, selector, *, timeout: int = 0):
            self.call_log.append(("wait_for_selector", (selector,)))
            if selector == field_sel:
                self.field_waits += 1
                if self.field_waits == 1:
                    raise PlaywrightTimeoutError(
                        "Business Unit fields not rendered until scroll"
                    )

    page = LazyBusinessUnitPage(
        locator_for={
            section_sel: section_locator,
            field_sel: FakeLocator(text="PACKAGING"),
            checked_sel: FakeLocator(match_count=1),
        },
    )
    lead = sf_client.LeadRow(
        record_id="00Q5g00000XYZAbc",
        name="Lazy BU Lead",
        source_url="https://acme.lightning.force.com/report",
    )

    result = sf_client.populate_is_packaging(
        page=page,
        lead=lead,
        salesforce_org_url="https://acme.lightning.force.com",
    )

    assert result.is_packaging is True
    assert section_locator.scroll_calls == 1
    waits = [c[1][0] for c in page.call_log if c[0] == "wait_for_selector"]
    assert waits == [field_sel, field_sel]


def test_populate_is_packaging_fails_closed_when_business_unit_section_missing():
    """Missing/unreadable Business Unit section must not enrich or write."""

    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    field_sel = selectors.SF_RECORD_DETAIL_BUSINESS_UNIT_PACKAGING_FIELD
    page = FakePage(
        wait_for_selector_raises_for={
            field_sel: PlaywrightTimeoutError(
                "Page.wait_for_selector: Timeout 30000ms exceeded."
            ),
        },
    )
    lead = sf_client.LeadRow(
        record_id="00Q5g00000XYZAbc",
        name="Unknown BU Lead",
        source_url="https://acme.lightning.force.com/report",
    )

    result = sf_client.populate_is_packaging(
        page=page,
        lead=lead,
        salesforce_org_url="https://acme.lightning.force.com",
    )

    assert result.is_packaging is False
    waits = [c[1][0] for c in page.call_log if c[0] == "wait_for_selector"]
    assert waits == [field_sel, field_sel]
    assert not any(
        c == ("locator", (field_sel,)) for c in page.call_log
    ), "missing section must not read the PACKAGING field value"
