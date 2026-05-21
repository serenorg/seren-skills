"""Claude ultrasonic-angle generator (replaces claude_hypothesis).

Per #766: the prior `Hypothesis` synthesizer produced generic
"qualified or not?" prose. Nathan's template needs three product-fit
angles per Lead — the selling-thesis input the assigned SAL uses to
prep a pitch. The angles are HU-specific (lidding-film sealing,
cold-chain compat, mono-material recyclable lidding, NQA-1
compliance, fiber-based lidstock, FIBC superback liner welding, etc).

The wrapper does not retry, does not reformat, and does not
hallucinate angles on an empty Claude response — an empty list flows
through to the renderer which surfaces `(not surfaced)` per the
Nathan-template pattern.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from scripts.seren_client import Fetcher, call_publisher


@dataclass(frozen=True)
class UltrasonicAngles:
    """Structured selling-thesis input for one Lead.

    `angles` is up to 3 bullet-form sentences. The renderer prefixes
    each with `•` and folds them under Nathan's `ULTRASONIC WELDING
    OPPORTUNITY` block.
    """

    angles: list[str] = field(default_factory=list)


# Lines beginning with `•`, `-`, or `1) … 9)` count as angles. The
# regex also tolerates leading whitespace and surrounding markdown
# bold tokens (`**`) that Claude sometimes emits.
_BULLET_RE = re.compile(r"^\s*(?:[-•]|\d+[\.)])\s*\*{0,2}(.+?)\*{0,2}\s*$")


def _parse_angles(text: str) -> list[str]:
    """Pull up to 3 bullet-prefixed lines out of `text`."""

    angles: list[str] = []
    for line in text.splitlines():
        match = _BULLET_RE.match(line)
        if match:
            angle = match.group(1).strip()
            if angle:
                angles.append(angle)
        if len(angles) >= 3:
            break
    return angles


_PROMPT_TEMPLATE = """\
You are preparing a packaging-sales pitch brief for Herrmann
Ultrasonics' Packaging division. We sell ultrasonic welding equipment
that joins flexible packaging films without adhesives or heat fusion.

Lead: {lead_name}
Company: {company_name}
Research summary: {perplexity_summary}

Write exactly three product-fit angles tying the company's actual
products + packaging operations to specific ultrasonic-welding levers.
Reference concrete packaging-welding angles such as: lidding-film
sealing through moisture / juice / powder residue; cold-chain seal
compatibility (no sustained product-zone heat); mono-material or
fiber-based recyclable lidstock; FIBC / superback liner closure
seam strength; NQA-1 nuclear-grade or food-grade contamination
control; gusseted-pouch or stand-up-pouch seal speed.

Format: three lines, one angle per line, each prefixed `• `.
Do not output any other text — no preamble, no numbering, no headers.
"""


def generate(
    *,
    lead_name: str,
    company_name: str,
    perplexity_summary: str,
    fetcher: Optional[Fetcher] = None,
    api_key: Optional[str] = None,
) -> UltrasonicAngles:
    """One Claude synthesis call. Returns parsed `UltrasonicAngles`."""

    prompt = _PROMPT_TEMPLATE.format(
        lead_name=lead_name,
        company_name=company_name or "(company unknown)",
        perplexity_summary=perplexity_summary or "(none)",
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
    return UltrasonicAngles(angles=_parse_angles(text))
