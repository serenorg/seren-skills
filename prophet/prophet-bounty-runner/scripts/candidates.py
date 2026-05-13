"""Phase 9 — candidate generation/scoring/filtering (plan §15).

The bounty runner consumes Polymarket sources rather than the static
template universe used by `prophet-market-seeder.scripts.agent`. The
seeder's `generate_market_candidates` is template-driven and not a fit;
per plan §15.1 this module copies the smallest standalone slice — the
`MarketCandidate` shape and the score heuristic — and wraps them in a
thin polymarket-aware adapter.

Duplicated from `prophet/prophet-market-seeder/scripts/agent.py`:
  - score heuristic (clarity / has_date / category-diversity)

Tracked in notes.md so the DRY pass after both skills package cleanly
can collapse them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polymarket.discovery import PolymarketSource

MIN_CANDIDATE_SCORE = 0.5

_DATE_KEYWORDS = ("by", "in 20", "Q1", "Q2", "Q3", "Q4")


@dataclass
class Candidate:
    polymarket_market_id: str
    question: str
    category: str
    score: float = 0.0
    payload: dict[str, Any] = field(default_factory=dict)


def generate_candidates(
    polymarket_sources: list["PolymarketSource"], *, n: int
) -> list[Candidate]:
    """Mirror each Polymarket source into a Candidate, capped at `n`."""
    keepers: list[Candidate] = []
    for source in polymarket_sources:
        if len(keepers) >= n:
            break
        # Issue #520: ship the enriched display question (event title +
        # group item title) into the candidate, not the bare child
        # predicate. Prophet's `/create` Validate Question step rejects
        # ambiguous strings without parent-series context.
        keepers.append(
            Candidate(
                polymarket_market_id=source.polymarket_market_id,
                question=source.display_question,
                category=source.category or "uncategorized",
                payload={
                    "source_resolution_date": source.resolution_date.isoformat(),
                    "polymarket_gamma_id": source.polymarket_gamma_id,
                    "polymarket_slug": source.slug,
                    "event_title": source.event_title,
                    "event_slug": source.event_slug,
                    "group_item_title": source.group_item_title,
                    "raw_question": source.question,
                },
            )
        )
    return keepers


def score_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Score on clarity / date-specificity / category diversity, sort desc."""
    seen_categories: dict[str, int] = {}
    for c in candidates:
        seen_categories[c.category] = seen_categories.get(c.category, 0) + 1
        clarity = 1.0 if c.question.endswith("?") else 0.5
        has_date = 1.0 if any(w in c.question for w in _DATE_KEYWORDS) else 0.3
        diversity = 1.0 / seen_categories[c.category]
        c.score = round(clarity * 0.3 + has_date * 0.3 + diversity * 0.4, 4)
    return sorted(candidates, key=lambda c: c.score, reverse=True)


def filter_candidates(
    candidates: list[Candidate], *, submit_limit: int
) -> list[Candidate]:
    """Drop sub-threshold candidates, then cap at `submit_limit`.

    Threshold protects the bounty pool: a low-clarity candidate that slips
    through to createMarket costs gas + a Prophet review slot but is
    unlikely to attract trade volume during the verifier window. Better
    to submit fewer high-quality markets than to fill the slot quota with
    weak ones.
    """
    eligible = [c for c in candidates if c.score >= MIN_CANDIDATE_SCORE]
    return eligible[:submit_limit]
