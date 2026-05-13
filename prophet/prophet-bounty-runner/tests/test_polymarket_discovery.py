"""Phase 8 — Polymarket source discovery (plan §14).

Critical-only tests cover the four filters that gate which markets reach
the Prophet createMarket call. Each filter is fail-closed: a Polymarket row
that does not unambiguously satisfy the deadline gate must NOT propagate
forward, because creating an out-of-window market on Prophet costs the user
gas and time and earns nothing from the bounty.

  1. Deadline filter — markets resolving on/after 2026-05-24T00:00:00Z are
     dropped (the bounty's hard deadline; anything later cannot resolve in
     time to earn).
  2. Past-cutoff filter — markets resolving on/before `now` are dropped.
     Polymarket Gamma keeps UMA-stuck markets (endDate in the past, but
     never formally `closed`) in the "active" pool. Mirroring one onto
     Prophet would create a market whose underlying event already
     happened. Live probe (2026-05-13) confirmed the bug: a query with
     only `end_date_max` returned Harvey Weinstein sentencing markets
     dated 2025-12-31, all `closed=false active=true`.
  3. Settled filter — already-settled Polymarket markets are dropped (no
     point mirroring something the source already finalized).
  4. Missing resolution_date — fail closed; without a resolution date we
     cannot prove either gate, so the row is excluded rather than allowed
     through with a guess.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from polymarket.discovery import (  # type: ignore[import-not-found]
    PolymarketSource,
    discover_polymarket_sources,
)

from conftest import load_fixture  # type: ignore[import-not-found]


DEADLINE = datetime(2026, 5, 24, 0, 0, 0, tzinfo=timezone.utc)
# Pin a deterministic "today" so test fixtures register against a
# stable URL. The existing discovery fixtures resolve in [2026-05-09,
# 2026-05-10]; a 2026-05-08 floor keeps every legacy fixture row
# in-window while still proving the past-cutoff filter rejects rows
# resolving at or before this instant.
NOW = datetime(2026, 5, 8, 0, 0, 0, tzinfo=timezone.utc)


_LIVE_PATH = (
    "/markets?end_date_min=2026-05-08T00:00:00Z"
    "&end_date_max=2026-05-24T00:00:00Z"
    "&closed=false&active=true&order=endDate&ascending=true&limit=500"
)


def _seed_polymarket(stub_gateway, payload) -> None:
    # Phase-14: discovery now sends an explicit deadline-bounded query
    # string. Register on the full path so production + test paths match
    # exactly. `payload` accepts either the legacy `{sources: [...]}`
    # envelope or a flat list, since the production code tolerates both.
    stub_gateway.register("polymarket-data", "GET", _LIVE_PATH, payload)


def test_market_resolving_at_or_after_deadline_is_excluded(stub_gateway) -> None:
    _seed_polymarket(stub_gateway, load_fixture("polymarket_settling.json"))

    sources = discover_polymarket_sources(
        gateway=stub_gateway, deadline=DEADLINE, now=NOW
    )

    ids = {s.polymarket_market_id for s in sources}
    assert "0xpoly-001" in ids  # resolves 2026-05-10 → before deadline → kept
    assert "0xpoly-002" in ids  # resolves 2026-05-09 → before deadline → kept
    assert "0xpoly-003" not in ids  # resolves 2026-05-30 → after deadline → dropped


def test_market_resolving_in_past_is_excluded(stub_gateway) -> None:
    """Live-bug regression (2026-05-13): Gamma returns UMA-stuck markets
    whose endDate is months in the past but `closed=false active=true`.
    Without a past-cutoff gate they would be proposed as bounty
    candidates, and the agent would create a Prophet market for an
    event that already resolved off-chain.
    """
    _seed_polymarket(
        stub_gateway,
        {
            "sources": [
                {
                    "polymarket_market_id": "0xpoly-stale",
                    "question": "Will Harvey Weinstein be sentenced to no prison time?",
                    "resolution_date": "2025-12-31T12:00:00Z",
                    "category": "legal",
                    "settled": False,
                },
                {
                    "polymarket_market_id": "0xpoly-fresh",
                    "question": "Will event Z happen by May 10 2026?",
                    "resolution_date": "2026-05-10T00:00:00Z",
                    "category": "macro",
                    "settled": False,
                },
            ]
        },
    )

    sources = discover_polymarket_sources(
        gateway=stub_gateway, deadline=DEADLINE, now=NOW
    )

    ids = {s.polymarket_market_id for s in sources}
    assert ids == {"0xpoly-fresh"}


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

    sources = discover_polymarket_sources(
        gateway=stub_gateway, deadline=DEADLINE, now=NOW
    )

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

    sources = discover_polymarket_sources(
        gateway=stub_gateway, deadline=DEADLINE, now=NOW
    )

    ids = {s.polymarket_market_id for s in sources}
    assert ids == {"0xpoly-good"}


def test_polymarket_source_dataclass_carries_normalized_fields(stub_gateway) -> None:
    _seed_polymarket(stub_gateway, load_fixture("polymarket_settling.json"))

    sources = discover_polymarket_sources(
        gateway=stub_gateway, deadline=DEADLINE, now=NOW
    )

    source_001 = next(s for s in sources if s.polymarket_market_id == "0xpoly-001")
    assert isinstance(source_001, PolymarketSource)
    assert source_001.question == "Will BTC close above $100k on May 10 2026?"
    assert source_001.resolution_date == datetime(2026, 5, 10, 23, 59, 0, tzinfo=timezone.utc)
    assert source_001.category == "crypto"
    assert source_001.settled is False
