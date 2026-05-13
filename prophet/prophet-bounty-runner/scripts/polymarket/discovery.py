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
# Phase-14 live probe (2026-05-08): the seren `polymarket-data` publisher
# exposes Polymarket's Gamma API directly. Default `/markets` returns 20
# arbitrary rows; we narrow to deadline-eligible open markets server-side
# so the response we filter is already pre-trimmed.
_BASE_PATH = "/markets"
# Phase 15 (#505): bumped from 100 to 500 so the discovery pass has
# enough headroom to find qualifiers inside Prophet's tight
# [2026-05-11, 2026-05-24] resolution window.
_DEFAULT_LIMIT = 500


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
    deadline_utc = _ensure_utc(deadline)
    deadline_iso = deadline_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    path = (
        f"{_BASE_PATH}?end_date_max={deadline_iso}"
        f"&closed=false&active=true&limit={_DEFAULT_LIMIT}"
    )
    response = gateway.call(PUBLISHER, "GET", path, body=None)
    raw_sources = _extract_sources(response)

    keepers: list[PolymarketSource] = []
    for raw in raw_sources:
        if not isinstance(raw, dict):
            continue
        # Polymarket Gamma exposes per-market lifecycle as `closed` (true
        # once resolution is finalized). Fall back to `archived` /
        # `resolved` if `closed` is absent — the publisher mirrors the
        # source verbatim and shapes drift between vintages.
        # `closed` is the live publisher field; `settled` is the legacy
        # name preserved by older fixtures and downstream consumers.
        if any(raw.get(k) is True for k in ("closed", "archived", "resolved", "settled")):
            continue
        # Field-name mapping (live probe 2026-05-08): the publisher
        # returns Polymarket's native shape. `conditionId` is the
        # canonical on-chain identifier; `endDate` is ISO-8601 UTC.
        resolution_date = _parse_resolution_date(
            raw.get("endDate") or raw.get("endDateIso") or raw.get("resolution_date")
        )
        if resolution_date is None:
            continue
        if resolution_date >= deadline_utc:
            continue
        market_id = raw.get("conditionId") or raw.get("id") or raw.get("polymarket_market_id")
        question = raw.get("question")
        if not isinstance(market_id, str) or not market_id:
            continue
        if not isinstance(question, str) or not question:
            continue
        category = _extract_category(raw)
        keepers.append(
            PolymarketSource(
                polymarket_market_id=market_id,
                question=question,
                resolution_date=resolution_date,
                category=category,
                settled=False,
            )
        )
    return keepers


def _extract_sources(response: Any) -> list[Any]:
    # Live probe 2026-05-08: the seren `polymarket-data` publisher returns
    # a flat list of market objects. Tolerate the legacy `{sources: [...]}`
    # envelope too so test fixtures keep working.
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        sources = response.get("sources") or response.get("markets") or response.get("data")
        if isinstance(sources, list):
            return sources
    return []


def _extract_category(raw: dict[str, Any]) -> str | None:
    direct = raw.get("category")
    if isinstance(direct, str) and direct:
        return direct
    # Polymarket nests category under `events[0].category` for some shapes.
    events = raw.get("events")
    if isinstance(events, list) and events:
        first = events[0]
        if isinstance(first, dict):
            cat = first.get("category")
            if isinstance(cat, str) and cat:
                return cat
    return None


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
