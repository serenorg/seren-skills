"""Phase 8 — Polymarket source discovery (plan §14).

Critical-only tests cover the three filters that gate which markets reach
the Prophet createMarket call. Each filter is fail-closed: a Polymarket row
that does not unambiguously satisfy the deadline gate must NOT propagate
forward, because creating an out-of-window market on Prophet costs the user
gas and time and earns nothing from the bounty.

  1. Deadline filter — markets resolving on/after 2026-05-11T00:00:00Z are
     dropped (the bounty's hard deadline; anything later cannot resolve in
     time to earn).
  2. Settled filter — already-settled Polymarket markets are dropped (no
     point mirroring something the source already finalized).
  3. Missing resolution_date — fail closed; without a resolution date we
     cannot prove the deadline gate, so the row is excluded rather than
     allowed through with a guess.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from polymarket.discovery import (  # type: ignore[import-not-found]
    PolymarketSource,
    discover_polymarket_sources,
)

from conftest import load_fixture  # type: ignore[import-not-found]


DEADLINE = datetime(2026, 5, 11, 0, 0, 0, tzinfo=timezone.utc)


def _seed_polymarket(stub_gateway, payload: dict) -> None:
    stub_gateway.register("polymarket-data", "GET", "/markets", payload)


def test_market_resolving_at_or_after_deadline_is_excluded(stub_gateway) -> None:
    _seed_polymarket(stub_gateway, load_fixture("polymarket_settling.json"))

    sources = discover_polymarket_sources(gateway=stub_gateway, deadline=DEADLINE)

    ids = {s.polymarket_market_id for s in sources}
    assert "0xpoly-001" in ids  # resolves 2026-05-10 → before deadline → kept
    assert "0xpoly-002" in ids  # resolves 2026-05-09 → before deadline → kept
    assert "0xpoly-003" not in ids  # resolves 2026-05-15 → after deadline → dropped


def test_already_settled_market_is_excluded(stub_gateway) -> None:
    _seed_polymarket(
        stub_gateway,
        {
            "sources": [
                {
                    "polymarket_market_id": "0xpoly-settled",
                    "question": "Did event X happen?",
                    "resolution_date": "2026-05-10T00:00:00Z",
                    "category": "crypto",
                    "settled": True,
                },
                {
                    "polymarket_market_id": "0xpoly-open",
                    "question": "Will event Y happen?",
                    "resolution_date": "2026-05-10T00:00:00Z",
                    "category": "crypto",
                    "settled": False,
                },
            ]
        },
    )

    sources = discover_polymarket_sources(gateway=stub_gateway, deadline=DEADLINE)

    ids = {s.polymarket_market_id for s in sources}
    assert ids == {"0xpoly-open"}


def test_market_with_missing_resolution_date_is_excluded(stub_gateway) -> None:
    _seed_polymarket(
        stub_gateway,
        {
            "sources": [
                {
                    "polymarket_market_id": "0xpoly-no-date",
                    "question": "When does this resolve?",
                    "category": "macro",
                    "settled": False,
                },
                {
                    "polymarket_market_id": "0xpoly-good",
                    "question": "Well-formed market.",
                    "resolution_date": "2026-05-09T18:00:00Z",
                    "category": "macro",
                    "settled": False,
                },
            ]
        },
    )

    sources = discover_polymarket_sources(gateway=stub_gateway, deadline=DEADLINE)

    ids = {s.polymarket_market_id for s in sources}
    assert ids == {"0xpoly-good"}


def test_polymarket_source_dataclass_carries_normalized_fields(stub_gateway) -> None:
    _seed_polymarket(stub_gateway, load_fixture("polymarket_settling.json"))

    sources = discover_polymarket_sources(gateway=stub_gateway, deadline=DEADLINE)

    source_001 = next(s for s in sources if s.polymarket_market_id == "0xpoly-001")
    assert isinstance(source_001, PolymarketSource)
    assert source_001.question == "Will BTC close above $100k on May 10 2026?"
    assert source_001.resolution_date == datetime(2026, 5, 10, 23, 59, 0, tzinfo=timezone.utc)
    assert source_001.category == "crypto"
    assert source_001.settled is False
