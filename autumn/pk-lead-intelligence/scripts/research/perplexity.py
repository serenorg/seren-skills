"""Perplexity research adapter (Nathan-template extractor).

Two jobs per call:

1. Return a narrative summary + citation list (unchanged from the
   pre-#766 contract — downstream callers still use `summary`).
2. Extract structured commercial detail (company / website / address /
   services / ICPs / markets / products / contact title-email-tenure /
   owner notes) so the Note renderer can fill Nathan's labeled-block
   template directly.

The extractor parses labeled blocks out of the model response. Our
prompt asks Perplexity to emit Nathan's template structure verbatim;
if the response drifts, the parser fills empty fields rather than
crashing the cycle. The renderer then surfaces `(not surfaced)`
markers per Nathan's "Not publicly listed" pattern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from scripts.seren_client import Fetcher, call_publisher


# --------------------------------------------------------------------- #
# Result types                                                          #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class CompanyExtract:
    """Structured commercial detail pulled from the Perplexity response.

    Fields map 1:1 to Nathan's labeled blocks. Empty defaults so a
    partial response renders cleanly with empty markers downstream.
    """

    company_name: str = ""
    website: str = ""
    address: str = ""
    top_services: list[str] = field(default_factory=list)
    top_icps: str = ""
    markets_served: str = ""
    key_products_made: str = ""
    contact_title: str = ""
    contact_email: str = ""
    contact_tenure_company: str = ""
    contact_tenure_role: str = ""
    owner_notes: str = ""


@dataclass(frozen=True)
class PerplexityResearch:
    """Normalized result of one Perplexity research call.

    `summary` is the narrative paragraph (legacy contract).
    `extract` is the structured Nathan-template payload (#766).
    `raw_text` is preserved so a future re-render can recompute the
    summary without re-charging SerenBucks.
    """

    summary: str
    citations: list[str]
    raw_text: str
    extract: Optional[CompanyExtract] = None


# --------------------------------------------------------------------- #
# Parser                                                                #
# --------------------------------------------------------------------- #


# Labels we ask Perplexity to emit, in the order Nathan's template lists
# them. Order is not load-bearing for parsing — the parser splits on
# label occurrences anywhere in the text.
_LABEL_TO_FIELD = {
    "COMPANY": "_company",
    "TOP 3 SERVICES / PRODUCT LINES": "top_services_raw",
    "TOP ICPs": "top_icps",
    "MARKETS SERVED": "markets_served",
    "KEY PRODUCTS MADE": "key_products_made",
    "CONTACT": "_contact",
    "OWNER NOTES": "owner_notes",
}


def _split_into_blocks(text: str) -> dict[str, str]:
    """Split labeled-block text into {label: body} pairs.

    Walks lines top-to-bottom. A line whose stripped form is a known
    label opens a new block; subsequent lines append to that block's
    body until the next label. Lines before any known label are
    ignored (the model often emits a leading SUMMARY block we don't
    use here — the narrative summary comes from the first paragraph).
    """

    blocks: dict[str, list[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in _LABEL_TO_FIELD:
            current = stripped
            blocks.setdefault(current, [])
            continue
        if current is not None:
            blocks[current].append(line)

    return {label: "\n".join(body).strip() for label, body in blocks.items()}


def _parse_company_block(body: str) -> tuple[str, str, str]:
    """Pull (name, website, address) out of the COMPANY block.

    Nathan's template shape:
        Fresh Pak, Inc.
        Website: https://freshpakinc.com
        Address on file: 7939 W Lafayette Blvd, Detroit MI 48209

    The first non-empty line is the company name. Subsequent lines
    match prefixes (`Website:` / `Address on file:` etc.) — anything
    that does not match falls through silently rather than crashing.
    """

    name = ""
    website = ""
    address = ""
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if lowered.startswith("website:"):
            website = stripped.split(":", 1)[1].strip()
        elif lowered.startswith("address on file:") or lowered.startswith(
            "address:"
        ):
            address = stripped.split(":", 1)[1].strip()
        elif not name:
            name = stripped
    return name, website, address


_NUMBERED_LIST_RE = re.compile(r"^\s*(?:\d+[\.)]|[-•])\s*(.+)$")


def _parse_numbered_list(body: str) -> list[str]:
    """Extract `1) … 2) …` or `• …` items into a flat string list."""

    items: list[str] = []
    for line in body.splitlines():
        match = _NUMBERED_LIST_RE.match(line)
        if match:
            items.append(match.group(1).strip())
    return items


def _parse_contact_block(body: str) -> tuple[str, str, str, str]:
    """Pull (title, email, tenure_company, tenure_role) out of CONTACT.

    Nathan's template:
        Name:     Jose Zamudio
        Title:    Operations Manager
        Email:    jzamudio@freshpakinc.com
        LinkedIn: ...
        Tenure at company:       Not publicly available
        Tenure in current role:  Not publicly available

    Name + LinkedIn flow through lead.name / linkedin_search separately,
    so we don't extract them here — they would override the live data.
    """

    title = ""
    email = ""
    tenure_company = ""
    tenure_role = ""
    for line in body.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith("title:"):
            title = stripped.split(":", 1)[1].strip()
        elif lowered.startswith("email:"):
            email = stripped.split(":", 1)[1].strip()
        elif lowered.startswith("tenure at company:"):
            tenure_company = stripped.split(":", 1)[1].strip()
        elif lowered.startswith("tenure in current role:"):
            tenure_role = stripped.split(":", 1)[1].strip()
    return title, email, tenure_company, tenure_role


def _build_extract(text: str) -> Optional[CompanyExtract]:
    """Parse the full model response into a CompanyExtract.

    Returns `None` if not a single labeled block was found — the
    renderer treats `None` as "extract unavailable" and emits "(not
    surfaced)" markers per Nathan's pattern.
    """

    blocks = _split_into_blocks(text)
    if not blocks:
        return None

    name, website, address = _parse_company_block(blocks.get("COMPANY", ""))
    title, email, tenure_company, tenure_role = _parse_contact_block(
        blocks.get("CONTACT", "")
    )

    return CompanyExtract(
        company_name=name,
        website=website,
        address=address,
        top_services=_parse_numbered_list(
            blocks.get("TOP 3 SERVICES / PRODUCT LINES", "")
        ),
        top_icps=blocks.get("TOP ICPs", ""),
        markets_served=blocks.get("MARKETS SERVED", ""),
        key_products_made=blocks.get("KEY PRODUCTS MADE", ""),
        contact_title=title,
        contact_email=email,
        contact_tenure_company=tenure_company,
        contact_tenure_role=tenure_role,
        owner_notes=blocks.get("OWNER NOTES", ""),
    )


def _first_paragraph(text: str) -> str:
    """Return the first non-empty paragraph of `text`.

    Used to pull a single-paragraph summary out of a model response
    that often wraps the headline in an introductory line. The
    summary is what lands in the Note's research-narrative line.
    """

    # If the response starts with a SUMMARY label, pick the lines that
    # follow it up to the next blank line — that's the narrative the
    # operator reads.
    lines = text.splitlines()
    in_summary = False
    captured: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped == "SUMMARY":
            in_summary = True
            continue
        if in_summary:
            if not stripped:
                if captured:
                    break
                continue
            if stripped in _LABEL_TO_FIELD:
                break
            captured.append(stripped)
    if captured:
        return " ".join(captured)

    for chunk in text.split("\n\n"):
        stripped = chunk.strip()
        if stripped:
            return stripped
    return text.strip()


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


_PROMPT_TEMPLATE = """\
Research the company and decision-makers behind the lead {lead_name!r}.
Original source URL: {source_hint}

Return a structured response with these labeled blocks, in this order,
plain text only (no markdown), one block per heading:

SUMMARY
A 3-5 sentence factual paragraph for a B2B packaging sales rep.

COMPANY
{{company name on first line}}
Website: {{url or "Not publicly available"}}
Address on file: {{street, city state zip, or "Not publicly available"}}

TOP 3 SERVICES / PRODUCT LINES
1) {{service or product line}}
2) {{service or product line}}
3) {{service or product line}}

TOP ICPs
{{one paragraph of the customer's ideal customer profile}}

MARKETS SERVED
{{one paragraph: geography, headcount, founding year}}

KEY PRODUCTS MADE
{{one paragraph naming the company's physical products + materials}}

CONTACT
Title: {{job title or "Not publicly listed"}}
Email: {{best email or "Not publicly listed"}}
Tenure at company: {{e.g. "3 years" or "Not publicly available"}}
Tenure in current role: {{or "Not publicly available"}}

OWNER NOTES
{{who appears to own the inbound; any qualifier or due-diligence note}}

Cite sources inline.
"""


def research_lead(
    *,
    lead_name: str,
    source_hint: str,
    fetcher: Optional[Fetcher] = None,
    api_key: Optional[str] = None,
) -> PerplexityResearch:
    """One Perplexity call about a Lead. Returns a normalized result.

    Reads `choices[0].message.content` + `citations[]` from the
    publisher response; everything else is the parser's job.
    """

    prompt = _PROMPT_TEMPLATE.format(
        lead_name=lead_name,
        source_hint=source_hint or "(unknown)",
    )
    body = {
        "model": "sonar",
        "messages": [{"role": "user", "content": prompt}],
    }
    response = call_publisher(
        "perplexity",
        "POST",
        "/chat/completions",
        body=body,
        api_key=api_key,
        fetcher=fetcher,
    )

    choices = response.get("choices") or []
    text = ""
    if choices:
        text = ((choices[0] or {}).get("message") or {}).get("content") or ""
    raw_citations = response.get("citations") or []
    citations: list[str] = []
    for item in raw_citations:
        if isinstance(item, str):
            citations.append(item)
        elif isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url:
                citations.append(url)

    summary = _first_paragraph(text) if text else ""
    extract = _build_extract(text) if text else None

    return PerplexityResearch(
        summary=summary,
        citations=citations,
        raw_text=text,
        extract=extract,
    )
