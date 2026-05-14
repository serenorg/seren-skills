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
    href: str | None
    text: str

    @property
    def first(self):
        # The production code chains `.first` to handle Playwright's
        # strict-mode multi-match; the fake collapses that to a
        # single-match identity so the rest of the assertions stay
        # readable.
        return self

    def get_attribute(self, name):
        assert name == "href"
        return self.href

    def inner_text(self):
        return self.text


@dataclass
class FakePage:
    locator_for: dict[str, FakeLocator] = field(default_factory=dict)
    call_log: list[tuple[str, tuple]] = field(default_factory=list)
    url: str = ""

    def goto(self, url, *, timeout: int = 0):
        self.call_log.append(("goto", (url,)))
        self.url = url

    def wait_for_selector(self, selector, *, timeout: int = 0):
        self.call_log.append(("wait_for_selector", (selector,)))

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


def test_fetch_first_lead_raises_when_href_missing():
    page = _make_page_with_link(href=None, text="Acme GmbH")
    with pytest.raises(RuntimeError, match="missing href"):
        sf_client.fetch_first_lead(
            page=page,
            salesforce_org_url="https://acme.lightning.force.com",
        )


def test_fetch_first_lead_raises_when_href_empty_string():
    page = _make_page_with_link(href="", text="Acme GmbH")
    with pytest.raises(RuntimeError, match="missing href"):
        sf_client.fetch_first_lead(
            page=page,
            salesforce_org_url="https://acme.lightning.force.com",
        )


def test_fetch_first_lead_raises_when_name_blank():
    page = _make_page_with_link(
        href="/lightning/r/Lead/00Q5g00000XYZAbc/view",
        text="   ",
    )
    with pytest.raises(RuntimeError, match="without text"):
        sf_client.fetch_first_lead(
            page=page,
            salesforce_org_url="https://acme.lightning.force.com",
        )
