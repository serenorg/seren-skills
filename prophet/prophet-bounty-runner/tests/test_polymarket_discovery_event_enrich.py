"""Issue #520: Polymarket child markets carry parent context under
`events[0].title` / `groupItemTitle` / `slug`, and Gamma's `id` is the
integer round-trippable lookup key — not the on-chain `conditionId`.
Discovery dropped all four on the floor.

Live evidence (2026-05-13 probe — esports markets in the bounty window):

    question        : 'Map 3: Odd/Even Total Rounds?'   ← ambiguous, no context
    events[0].title : 'Counter-Strike: Haunted House vs THUNDER dOWNUNDER (BO3) - Asian Champions League Group B'

Three critical tests — one per contract — no duplication:

1. discovery reads events[0].title + groupItemTitle + slug + events[0].slug
   + the Gamma integer `id`, and `display_question` assembles the enriched
   Prophet-ready string.
2. `polymarket_market_id` stays the conditionId (durable settlement key
   the reconciler matches against), and `polymarket_gamma_id` is the
   separate round-trippable lookup key — they are NOT the same value.
3. `generate_candidates` propagates the enriched display question through
   `Candidate.question`, which is what `pending_ui_submission[].question`
   ultimately carries into Prophet's `/create` UI.
"""

from __future__ import annotations

from datetime import datetime, timezone

from candidates import generate_candidates  # type: ignore[import-not-found]
from polymarket.discovery import (  # type: ignore[import-not-found]
    PolymarketSource,
    discover_polymarket_sources,
)


_DEADLINE = datetime(2026, 5, 24, 0, 0, 0, tzinfo=timezone.utc)
_NOW = datetime(2026, 5, 13, 0, 0, 0, tzinfo=timezone.utc)

_LIVE_PATH = (
    "/markets?end_date_min=2026-05-13T00:00:00Z"
    "&end_date_max=2026-05-24T00:00:00Z"
    "&closed=false&active=true&order=endDate&ascending=true&limit=500"
)

# Mirror of an actual in-window Gamma esports market shape captured live
# during the audit. The child `question` lacks all context; everything
# Prophet's Validate Question step needs lives under `groupItemTitle` and
# `events[0].title`.
_ESPORTS_MARKET = {
    "id": "2249068",
    "conditionId": "0xb8c7fab939b48030c2221efa21380574fbee88cde02cf33aee5ef0d0f0cf228c",
    "question": "Map 3: Odd/Even Total Rounds?",
    "groupItemTitle": "Odd/Even Total Rounds",
    "slug": "cs2-hh1-thunde-2026-05-13-game3-odd-even-total-rounds",
    "endDate": "2026-05-13T17:30:00Z",
    "closed": False,
    "active": True,
    "events": [
        {
            "title": "Counter-Strike: Haunted House vs THUNDER dOWNUNDER (BO3) - Asian Champions League Group B",
            "slug": "cs2-hh1-thunde-2026-05-13",
            "category": "Esports",
        }
    ],
}


def test_discovery_extracts_event_context_and_normalizes_question(stub_gateway) -> None:
    stub_gateway.register("polymarket-data", "GET", _LIVE_PATH, [_ESPORTS_MARKET])

    sources = discover_polymarket_sources(
        gateway=stub_gateway, deadline=_DEADLINE, now=_NOW
    )

    assert len(sources) == 1
    src = sources[0]
    assert src.polymarket_gamma_id == "2249068"
    assert src.slug == "cs2-hh1-thunde-2026-05-13-game3-odd-even-total-rounds"
    assert src.event_title == (
        "Counter-Strike: Haunted House vs THUNDER dOWNUNDER (BO3)"
        " - Asian Champions League Group B"
    )
    assert src.event_slug == "cs2-hh1-thunde-2026-05-13"
    assert src.group_item_title == "Odd/Even Total Rounds"
    # The display_question must carry parent series context, not the bare
    # child predicate. Prophet's Validate Question rejects ambiguous strings.
    assert "Counter-Strike: Haunted House vs THUNDER dOWNUNDER" in src.display_question
    assert "Odd/Even Total Rounds" in src.display_question


def test_polymarket_market_id_is_condition_id_not_gamma_id(stub_gateway) -> None:
    """Pin the round-trip contract: `polymarket_market_id` is the CTF
    conditionId (durable cross-system settlement key), `polymarket_gamma_id`
    is the separate Gamma integer used for `/markets?id=...` lookups. The
    two MUST NOT be conflated — Gamma 422s on conditionId lookups.
    """
    stub_gateway.register("polymarket-data", "GET", _LIVE_PATH, [_ESPORTS_MARKET])

    src = discover_polymarket_sources(
        gateway=stub_gateway, deadline=_DEADLINE, now=_NOW
    )[0]

    assert src.polymarket_market_id == (
        "0xb8c7fab939b48030c2221efa21380574fbee88cde02cf33aee5ef0d0f0cf228c"
    )
    assert src.polymarket_gamma_id == "2249068"
    assert src.polymarket_market_id != src.polymarket_gamma_id


def test_generate_candidates_propagates_display_question_to_candidate() -> None:
    """The Candidate.question field is what `pending_ui_submission[].question`
    ultimately ships to Prophet's `/create` UI. It must be the enriched
    display string, not the bare child predicate.
    """
    source = PolymarketSource(
        polymarket_market_id="0xb8c7fab9",
        polymarket_gamma_id="2249068",
        question="Map 3: Odd/Even Total Rounds?",
        resolution_date=datetime(2026, 5, 13, 17, 30, tzinfo=timezone.utc),
        category="Esports",
        settled=False,
        slug="cs2-hh1-thunde-2026-05-13-game3-odd-even-total-rounds",
        event_title=(
            "Counter-Strike: Haunted House vs THUNDER dOWNUNDER (BO3)"
            " - Asian Champions League Group B"
        ),
        event_slug="cs2-hh1-thunde-2026-05-13",
        group_item_title="Odd/Even Total Rounds",
    )

    candidates = generate_candidates([source], n=1)

    assert len(candidates) == 1
    assert "Counter-Strike: Haunted House vs THUNDER dOWNUNDER" in candidates[0].question
    assert "Odd/Even Total Rounds" in candidates[0].question
