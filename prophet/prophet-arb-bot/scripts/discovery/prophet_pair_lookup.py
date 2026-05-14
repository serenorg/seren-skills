"""Match Polymarket candidates to existing Prophet markets (#538).

When auto-discover surfaces a Polymarket candidate, the arb-bot first
asks Prophet whether the operator already created a mirror. If yes,
the pair is auto-bound and the existing scoring loop arbs it on the
next tick. If no, the candidate goes into `pending_ui_submission` for
the agent's Playwright `/create` runbook.

Match heuristic: normalize question text (lowercase + strip
punctuation + collapse whitespace), then check substring containment
in either direction with a configurable prefix length. Prophet creates
markets from the operator's exact spreadsheet, so the question text
should be near-identical — substring matching trades a small false-
positive risk for resilience to minor paraphrasing.
"""

from __future__ import annotations

import re
from typing import Any


_PUNCT_RE = re.compile(r"[^\w\s]+", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def _normalize_question(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Stable enough
    that "Will the Yankees beat the Orioles tonight?" and "Will the
    Yankees beat the Orioles tonight" produce the same key."""
    if not text:
        return ""
    lowered = text.lower()
    stripped = _PUNCT_RE.sub(" ", lowered)
    return _WS_RE.sub(" ", stripped).strip()


def find_matching_prophet_markets(
    *,
    prophet_client: Any,
    jwt: str | None,
    candidate_questions: dict[str, str],
    prefix_length: int = 60,
    market_list_limit: int = 200,
) -> dict[str, str]:
    """Return a mapping ``polymarket_condition_id -> prophet_market_id``
    for every candidate that matches a currently-listed Prophet market.

    Candidates without a match are absent from the returned dict — the
    caller treats them as "Prophet hasn't created this yet" and emits a
    `pending_ui_submission` entry.

    Failure modes:
      - If ``prophet_client.markets_for_dedup`` raises, we re-raise. The
        caller decides whether to fail closed (block the cycle) or fail
        soft (emit everything as pending_ui_submission). Default policy:
        soft, because a Prophet-side outage shouldn't block the agent
        from queuing new market creations the operator drives manually.
    """
    if not candidate_questions:
        return {}

    raw_markets = prophet_client.markets_for_dedup(
        jwt=jwt, limit=int(market_list_limit)
    )
    prophet_norm: list[tuple[str, str]] = []
    for market in raw_markets or []:
        if not isinstance(market, dict):
            continue
        market_id = market.get("id")
        question = market.get("question")
        if not isinstance(market_id, str) or not market_id:
            continue
        if not isinstance(question, str) or not question:
            continue
        prophet_norm.append((market_id, _normalize_question(question)))

    if not prophet_norm:
        return {}

    matched: dict[str, str] = {}
    for poly_id, poly_question in candidate_questions.items():
        cand_norm = _normalize_question(poly_question)
        if len(cand_norm) < 10:
            continue
        cand_prefix = cand_norm[: int(prefix_length)]
        for prophet_id, prophet_question_norm in prophet_norm:
            if not prophet_question_norm:
                continue
            prophet_prefix = prophet_question_norm[: int(prefix_length)]
            if cand_prefix in prophet_question_norm or prophet_prefix in cand_norm:
                matched[poly_id] = prophet_id
                break
    return matched
