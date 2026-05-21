"""Critical contract for the Perplexity research extractor.

The Perplexity adapter has to do two jobs now:

1. Return a narrative summary + citation list (existing).
2. Extract structured commercial detail (company / website / address /
   services / ICPs / markets / products / contact title-email-tenure /
   owner notes) so the Note renderer can fill Nathan's 11-block layout.

The extractor parses labeled blocks out of Perplexity's response text.
We test the parser against a fixture response shaped like Nathan's
template — if Perplexity drifts the schema, the parser fills
`(not surfaced)` rather than crashing the cycle.
"""

from __future__ import annotations

import json
from typing import Any

from scripts.research import perplexity


# The fixture response below is a labeled-block string in the exact
# shape we prompt Perplexity to emit. The parser's job is to turn it
# into a CompanyExtract.
_FIXTURE_RESPONSE = """\
SUMMARY
Fresh Pak processes fresh-cut produce out of Detroit MI.

COMPANY
Fresh Pak, Inc.
Website: https://freshpakinc.com
Address on file: 7939 W Lafayette Blvd, Detroit MI 48209

TOP 3 SERVICES / PRODUCT LINES
1) Fresh-cut produce processing
2) Private-label retail fresh-cut packaging
3) Foodservice ingredient supply

TOP ICPs
Retail grocery, foodservice operators, food manufacturers.

MARKETS SERVED
Detroit MI HQ; U.S. Midwest retail / foodservice.

KEY PRODUCTS MADE
Fresh-cut fruit, vegetable, and salad packs in private-label or branded packaging.

CONTACT
Title: Operations Manager
Email: jzamudio@freshpakinc.com
Tenure at company: Not publicly available
Tenure in current role: Not publicly available

OWNER NOTES
Erica Perry — inbound via Website Contact-Form.
"""


def _stub_fetcher(response_payload: dict[str, Any]):
    """Build a Fetcher stub that returns a canned publisher response.

    Matches the `Fetcher = Callable[[str, str, dict, Optional[bytes]],
    tuple[int, bytes]]` signature in `scripts/seren_client.py`. The
    response is JSON-encoded to bytes so the gateway-envelope unwrap
    path runs end-to-end through the stub.
    """

    encoded = json.dumps(response_payload).encode("utf-8")

    def fetcher(method: str, url: str, headers: dict, body: bytes | None):
        return (200, encoded)

    return fetcher


def test_research_lead_extracts_company_block() -> None:
    """CompanyExtract carries company / website / address from the response."""

    fetcher = _stub_fetcher(
        {
            "choices": [
                {"message": {"content": _FIXTURE_RESPONSE}}
            ],
            "citations": ["https://example.com/a"],
        }
    )

    result = perplexity.research_lead(
        lead_name="Jose Zamudio",
        source_hint="",
        fetcher=fetcher,
        api_key="sk-test",
    )

    assert result.extract is not None
    assert result.extract.company_name == "Fresh Pak, Inc."
    assert result.extract.website == "https://freshpakinc.com"
    assert "Detroit" in result.extract.address


def test_research_lead_extracts_top_services_as_list() -> None:
    fetcher = _stub_fetcher(
        {
            "choices": [{"message": {"content": _FIXTURE_RESPONSE}}],
            "citations": [],
        }
    )

    result = perplexity.research_lead(
        lead_name="Jose Zamudio",
        source_hint="",
        fetcher=fetcher,
        api_key="sk-test",
    )

    assert result.extract is not None
    # Three services parsed out of the `1) … 2) … 3) …` block.
    assert len(result.extract.top_services) == 3
    assert "Fresh-cut produce processing" in result.extract.top_services


def test_research_lead_extracts_contact_title_and_email() -> None:
    """CONTACT block must surface title + email so the SAL can reach out."""

    fetcher = _stub_fetcher(
        {
            "choices": [{"message": {"content": _FIXTURE_RESPONSE}}],
            "citations": [],
        }
    )

    result = perplexity.research_lead(
        lead_name="Jose Zamudio",
        source_hint="",
        fetcher=fetcher,
        api_key="sk-test",
    )

    assert result.extract is not None
    assert result.extract.contact_title == "Operations Manager"
    assert result.extract.contact_email == "jzamudio@freshpakinc.com"


def test_research_lead_handles_empty_response_gracefully() -> None:
    """Empty content -> extract is None, summary is empty. No crash."""

    fetcher = _stub_fetcher({"choices": [], "citations": []})

    result = perplexity.research_lead(
        lead_name="Jose Zamudio",
        source_hint="",
        fetcher=fetcher,
        api_key="sk-test",
    )

    # extract is None when nothing parseable came back — the renderer
    # surfaces "(not surfaced)" markers per Nathan's template style.
    assert result.extract is None
    assert result.summary == ""


def test_research_lead_handles_missing_blocks_gracefully() -> None:
    """Partial response -> CompanyExtract with empty fields for missing blocks."""

    partial = "COMPANY\nFresh Pak, Inc.\nWebsite: https://freshpakinc.com\n"
    fetcher = _stub_fetcher(
        {
            "choices": [{"message": {"content": partial}}],
            "citations": [],
        }
    )

    result = perplexity.research_lead(
        lead_name="Jose Zamudio",
        source_hint="",
        fetcher=fetcher,
        api_key="sk-test",
    )

    assert result.extract is not None
    assert result.extract.company_name == "Fresh Pak, Inc."
    # Missing blocks fall back to empty rather than crashing.
    assert result.extract.top_services == []
    assert result.extract.markets_served == ""
