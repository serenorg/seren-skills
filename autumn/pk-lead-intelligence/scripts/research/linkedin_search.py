"""LinkedIn discovery via search-engine queries (no bulk scraping).

Per SKILL.md "Privacy & Compliance": the skill must not scrape
LinkedIn directly. This module issues a site-restricted query through
the `perplexity` publisher to surface candidate profile URLs, then
scores each candidate with a pure match-confidence function over the
text the search engine returned.

The scoring function is a pure function and the only piece that needs
unit coverage — the discovery call is a thin HTTP wrapper exercised
end-to-end during the operator-driven dry-run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from scripts.seren_client import Fetcher, call_publisher


# --------------------------------------------------------------------- #
# Result type                                                           #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class LinkedInCandidate:
    """One ranked LinkedIn profile candidate for a Lead.

    `match_confidence` is a 0-100 integer produced by `score_candidate`
    and is what the Note surfaces to the operator. `reasons` lists the
    signals that contributed, so a reviewer can audit the score.
    """

    url: str
    title: Optional[str]
    match_confidence: int
    reasons: list[str]


# --------------------------------------------------------------------- #
# Pure scoring                                                          #
# --------------------------------------------------------------------- #


_TOKEN_SPLIT = re.compile(r"[^a-zA-Z]+")
_PROFILE_URL_RE = re.compile(r"https://www\.linkedin\.com/in/[A-Za-z0-9\-_]+")

_PROFILE_ANCHOR_POINTS = 20
_NAME_TOKEN_POOL_POINTS = 50
_COMPANY_HIT_POINTS = 10
_MAX_SCORE = 100


def _tokens(value: str) -> set[str]:
    """Lowercase, alpha-only tokens from a free-form string."""

    return {t.lower() for t in _TOKEN_SPLIT.split(value) if t}


def score_candidate(
    *,
    candidate_url: str,
    candidate_title: Optional[str],
    candidate_snippet: str,
    lead_name: str,
    company_hint: Optional[str],
) -> tuple[int, list[str]]:
    """Pure match-confidence scoring over one search result.

    Returns `(score, reasons)` where `score` is in `[0, 100]`. Reasons
    are short tags suitable for landing in a Note's audit trail.

    Signals:
      * `/in/` profile-URL anchor (20 points) — non-profile URLs score 0
      * lead-name tokens present in title+snippet (up to 50 points,
        scaled by fraction matched)
      * company-hint tokens present in title+snippet (one 10-point boost,
        not per-token, so repeated mentions cannot dominate)
    """

    reasons: list[str] = []
    if "/in/" not in candidate_url:
        return 0, ["url-not-profile"]

    score = _PROFILE_ANCHOR_POINTS
    reasons.append("linkedin-profile-url")

    haystack_parts = [candidate_title or "", candidate_snippet or ""]
    haystack = " ".join(haystack_parts).lower()

    name_tokens = _tokens(lead_name)
    if name_tokens:
        matched = {t for t in name_tokens if t in haystack}
        if matched:
            ratio = len(matched) / len(name_tokens)
            score += int(_NAME_TOKEN_POOL_POINTS * ratio)
            reasons.append(f"name-tokens:{','.join(sorted(matched))}")

    if company_hint:
        company_tokens = _tokens(company_hint)
        for tok in sorted(company_tokens):
            if tok in haystack:
                score += _COMPANY_HIT_POINTS
                reasons.append(f"company-token:{tok}")
                break  # single boost — repeated mentions cannot stack

    return min(score, _MAX_SCORE), reasons


# --------------------------------------------------------------------- #
# Discovery (thin HTTP wrapper — exercised in operator dry-run)         #
# --------------------------------------------------------------------- #


def discover_candidates(
    *,
    lead_name: str,
    company_hint: Optional[str],
    fetcher: Optional[Fetcher] = None,
    api_key: Optional[str] = None,
) -> list[LinkedInCandidate]:
    """Use the `perplexity` publisher to find candidate LinkedIn URLs.

    Sends a site-restricted query and parses linkedin.com/in/ profile
    URLs out of the model's response. Each candidate URL is then
    scored with `score_candidate` against the model's free-form
    response text (used as a snippet). Candidates are returned sorted
    by descending confidence.
    """

    quoted_name = lead_name.replace('"', "")
    query_parts = [f'site:linkedin.com/in/ "{quoted_name}"']
    if company_hint:
        quoted_company = company_hint.replace('"', "")
        query_parts.append(f'"{quoted_company}"')
    query = " ".join(query_parts)

    prompt = (
        "Find LinkedIn profile URLs matching this query. Return each "
        "URL on its own line — no commentary.\n\nQuery: " + query
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

    urls = list(dict.fromkeys(_PROFILE_URL_RE.findall(text)))
    candidates: list[LinkedInCandidate] = []
    for url in urls:
        score, reasons = score_candidate(
            candidate_url=url,
            candidate_title=None,
            candidate_snippet=text,
            lead_name=lead_name,
            company_hint=company_hint,
        )
        candidates.append(
            LinkedInCandidate(
                url=url, title=None, match_confidence=score, reasons=reasons
            )
        )
    candidates.sort(key=lambda c: c.match_confidence, reverse=True)
    return candidates
