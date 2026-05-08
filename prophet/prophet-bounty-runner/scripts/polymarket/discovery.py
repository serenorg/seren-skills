"""Polymarket source discovery — plan §14.

Fetches the settling-markets feed from the polymarket-data publisher and
applies three fail-closed filters before any row reaches candidate
generation:

  - deadline gate: resolution_date < deadline (strict <, not <=).
    Rows resolving exactly at the deadline are dropped because the
    bounty's verifier window closes on the same instant — a market
    resolving at deadline cannot be paid before the window shuts.
  - settled gate: settled=True rows are skipped (no value mirroring an
    already-finalized market).
  - parseable date gate: rows missing or with malformed resolution_date
    are dropped. Without a verifiable date we cannot prove the deadline
    gate, and creating an out-of-window Prophet market costs gas and
    earns nothing.

The Polymarket source row is normalized into PolymarketSource so the
downstream candidate generator does not have to handle raw publisher
JSON shape variations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

PUBLISHER = "polymarket-data"
PATH = "/markets"


@dataclass
class PolymarketSource:
    polymarket_market_id: str
    question: str
    resolution_date: datetime
    category: str | None
    settled: bool


def discover_polymarket_sources(
    *, gateway: Any, deadline: datetime
) -> list[PolymarketSource]:
    """Fetch settling-markets feed and return the deadline-eligible subset."""
    response = gateway.call(PUBLISHER, "GET", PATH, body=None)
    raw_sources = _extract_sources(response)
    deadline_utc = _ensure_utc(deadline)

    keepers: list[PolymarketSource] = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            continue
        if raw.get("settled") is True:
            continue
        resolution_date = _parse_resolution_date(raw.get("resolution_date"))
        if resolution_date is None:
            continue
        if resolution_date >= deadline_utc:
            continue
        market_id = raw.get("polymarket_market_id")
        question = raw.get("question")
        if not isinstance(market_id, str) or not market_id:
            continue
        if not isinstance(question, str) or not question:
            continue
        keepers.append(
            PolymarketSource(
                polymarket_market_id=market_id,
                question=question,
                resolution_date=resolution_date,
                category=raw.get("category") if isinstance(raw.get("category"), str) else None,
                settled=False,
            )
        )
    return keepers


def _extract_sources(response: Any) -> list[Any]:
    if not isinstance(response, dict):
        return []
    sources = response.get("sources")
    if not isinstance(sources, list):
        return []
    return sources


def _parse_resolution_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
