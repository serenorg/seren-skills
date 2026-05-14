"""Perplexity research adapter.

Thin wrapper over the `perplexity` Seren publisher. Calls the
OpenAI-shape `/chat/completions` endpoint with a structured prompt
about one Lead and returns a normalized `PerplexityResearch` bundle
for downstream rendering.

The wrapper does no synthesis of its own — keep the raw text on the
return value so the renderer (and any future re-render) can re-derive
the summary if the prompt format changes. The `summary` field is
populated with the first non-empty paragraph of the model's response,
which is the operator-readable "what does Perplexity say" line in the
Note.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from scripts.seren_client import Fetcher, call_publisher


@dataclass(frozen=True)
class PerplexityResearch:
    """Normalized result of one Perplexity research call.

    `summary` is what lands in the Note's Research Summary section.
    `raw_text` is preserved so a future re-render can recompute the
    summary without re-charging SerenBucks.
    """

    summary: str
    citations: list[str]
    raw_text: str


def _first_paragraph(text: str) -> str:
    """Return the first non-empty paragraph of `text`.

    Used to pull a single-paragraph summary out of a model response
    that often wraps the headline in an introductory line.
    """

    for chunk in text.split("\n\n"):
        stripped = chunk.strip()
        if stripped:
            return stripped
    return text.strip()


def research_lead(
    *,
    lead_name: str,
    source_hint: str,
    fetcher: Optional[Fetcher] = None,
    api_key: Optional[str] = None,
) -> PerplexityResearch:
    """One Perplexity call about a Lead. Returns a normalized result.

    The call only reads `choices[0].message.content` and `citations[]`
    from the response. The `perplexity` publisher proxies the upstream
    API and may add or rename fields without notice; we deliberately
    do not depend on anything else.
    """

    prompt = (
        f"Research the company and decision-makers behind the lead "
        f"{lead_name!r}.\n"
        f"Original source URL: {source_hint or '(unknown)'}.\n\n"
        "Return a concise (3-5 sentence) factual summary plus the most "
        "recent signals that would matter to a B2B packaging sales rep "
        "(funding events, capacity expansion, executive hires, public "
        "RFPs). Cite sources inline."
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
    # Citations may arrive as bare strings or as `{url, title}` dicts
    # depending on the upstream version. Normalize to a flat URL list.
    citations: list[str] = []
    for item in raw_citations:
        if isinstance(item, str):
            citations.append(item)
        elif isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url:
                citations.append(url)

    return PerplexityResearch(
        summary=_first_paragraph(text),
        citations=citations,
        raw_text=text,
    )
