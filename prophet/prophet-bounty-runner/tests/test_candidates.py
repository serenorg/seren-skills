"""Phase 9 — candidate generation/scoring/filtering (plan §15).

The bounty runner's flow is polymarket-source-driven, while the existing
prophet-market-seeder uses static templates. Per plan §15.1 we copy the
smallest standalone slice rather than refactor the source skill; the
imported logic is the score function, and a thin adapter wraps it for
PolymarketSource inputs.

Critical-only tests cover the four guarantees called out in §15.3.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from candidates import (  # type: ignore[import-not-found]
    Candidate,
    MIN_CANDIDATE_SCORE,
    filter_candidates,
    generate_candidates,
    score_candidates,
)
from polymarket.discovery import PolymarketSource  # type: ignore[import-not-found]


def _src(market_id: str, question: str, category: str = "crypto") -> PolymarketSource:
    return PolymarketSource(
        polymarket_market_id=market_id,
        question=question,
        resolution_date=datetime(2026, 5, 10, 0, 0, 0, tzinfo=timezone.utc),
        category=category,
        settled=False,
    )


def test_generate_candidates_emits_one_per_polymarket_source_up_to_limit() -> None:
    sources = [
        _src("0xpoly-001", "Will BTC close above $100k by Q2 2026?"),
        _src("0xpoly-002", "Will the Fed cut rates by May 2026?", category="macro"),
        _src("0xpoly-003", "Will event X resolve YES by 2026?"),
    ]

    candidates = generate_candidates(sources, n=2)

    assert len(candidates) == 2
    market_ids = [c.polymarket_market_id for c in candidates]
    assert market_ids == ["0xpoly-001", "0xpoly-002"]
    assert {c.question for c in candidates} == {sources[0].question, sources[1].question}


def test_generate_candidates_returns_all_when_limit_exceeds_sources() -> None:
    sources = [_src("0xpoly-001", "Question one?")]

    candidates = generate_candidates(sources, n=5)

    assert len(candidates) == 1
    assert candidates[0].polymarket_market_id == "0xpoly-001"


def test_score_candidates_returns_monotonic_scores() -> None:
    candidates = [
        Candidate(
            polymarket_market_id="0xpoly-low",
            question="vague statement no date",
            category="other",
        ),
        Candidate(
            polymarket_market_id="0xpoly-mid",
            question="Will Q2 happen?",
            category="macro",
        ),
        Candidate(
            polymarket_market_id="0xpoly-high",
            question="Will the Fed cut rates by Q2 2026?",
            category="crypto",
        ),
    ]

    scored = score_candidates(candidates)

    scores = [c.score for c in scored]
    assert scores == sorted(scores, reverse=True), f"scores not monotonic: {scores}"
    assert scored[0].polymarket_market_id != "0xpoly-low"


def test_filter_candidates_drops_below_threshold() -> None:
    high = Candidate(
        polymarket_market_id="0xpoly-high",
        question="Will the Fed cut rates by Q2 2026?",
        category="crypto",
        score=0.9,
    )
    low = Candidate(
        polymarket_market_id="0xpoly-low",
        question="vague statement no date",
        category="other",
        score=MIN_CANDIDATE_SCORE - 0.01,
    )

    filtered = filter_candidates([high, low], submit_limit=10)

    assert [c.polymarket_market_id for c in filtered] == ["0xpoly-high"]


def test_filter_candidates_respects_submit_limit() -> None:
    candidates = [
        Candidate(
            polymarket_market_id=f"0xpoly-{i}",
            question=f"Will event {i} happen by Q2 2026?",
            category="crypto",
            score=0.9 - i * 0.01,
        )
        for i in range(8)
    ]

    filtered = filter_candidates(candidates, submit_limit=3)

    assert len(filtered) == 3
    assert [c.polymarket_market_id for c in filtered] == [
        "0xpoly-0",
        "0xpoly-1",
        "0xpoly-2",
    ]
