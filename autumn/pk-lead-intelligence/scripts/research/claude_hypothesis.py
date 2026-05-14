"""Claude hypothesis synthesizer.

Calls the `seren-models` publisher with the Perplexity + LinkedIn
signals and asks Claude for a 2-3 sentence hypothesis plus exactly
one recommended next action. Both pieces are surfaced verbatim by the
renderer — Claude's output is the operator-readable judgment that the
human owner reviews.

The wrapper does not retry, does not reformat, and does not synthesize
a fallback recommendation when Claude returns malformed output — the
parser falls back to the raw text so the operator sees the failure
clearly instead of getting a hallucinated "Review manually" stub that
looks like a real action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from scripts.seren_client import Fetcher, call_publisher


@dataclass(frozen=True)
class Hypothesis:
    """Structured Claude output for one Lead.

    `text` lands in the Note's Hypothesis section. `recommended_action`
    lands in the Recommended Next Action section.
    """

    text: str
    recommended_action: str


_HYPOTHESIS_PREFIX = "hypothesis:"
_ACTION_PREFIX = "next action:"


def _parse(text: str) -> Hypothesis:
    """Pull the `Hypothesis:` and `Next action:` lines out of `text`.

    Falls back to the raw text + a clear "needs operator review"
    action when either field is missing. Surfacing the failure beats
    hallucinating a confident-sounding action that the operator might
    take at face value.
    """

    hypothesis = ""
    action = ""
    for line in text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if lowered.startswith(_HYPOTHESIS_PREFIX):
            hypothesis = stripped[len(_HYPOTHESIS_PREFIX):].strip()
        elif lowered.startswith(_ACTION_PREFIX):
            action = stripped[len(_ACTION_PREFIX):].strip()

    if not hypothesis:
        # Surface the unparsed model output rather than dropping it.
        hypothesis = text.strip()[:500] or "(model returned empty response)"
    if not action:
        action = "Needs operator review — Claude response did not include a next action."
    return Hypothesis(text=hypothesis, recommended_action=action)


def generate(
    *,
    lead_name: str,
    perplexity_summary: str,
    linkedin_url: Optional[str],
    fetcher: Optional[Fetcher] = None,
    api_key: Optional[str] = None,
) -> Hypothesis:
    """One Claude synthesis call. Returns a parsed `Hypothesis`."""

    prompt = (
        f"Lead: {lead_name}\n"
        f"LinkedIn: {linkedin_url or '(unknown)'}\n"
        f"Research summary: {perplexity_summary or '(none)'}\n\n"
        "Write a 2-3 sentence hypothesis about whether this is a "
        "qualified PK (Packaging) lead and why. Then write exactly one "
        "recommended next action. Use this format and nothing else:\n\n"
        "Hypothesis: <your 2-3 sentence hypothesis>\n"
        "Next action: <one concrete next step the sales rep should take>"
    )
    body = {
        "model": "anthropic/claude-sonnet-4-5",
        "messages": [{"role": "user", "content": prompt}],
    }
    response = call_publisher(
        "seren-models",
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
    return _parse(text)
