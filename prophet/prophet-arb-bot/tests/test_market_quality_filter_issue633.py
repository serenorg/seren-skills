"""Issue #633 — auto-discover quality filter + tighter slippage default.

Three critical tests, no duplicates:

1. ``test_market_quality_filter_accept_reject_matrix`` — parametrized
   accept/reject for ``is_high_quality_resolution`` covering all
   classes the operator's live-cycle data exposed:
   - accept: sports vs-match, binary financial threshold, named election
   - reject: tweet-count buckets, subjective political events,
     date-bounded geopolitical events, unknown patterns (default-deny)

2. ``test_auto_discover_excludes_low_quality_markets`` — integration
   check that ``discover_arb_candidates_with_stats`` actually applies
   the filter: a mixed fixture of good + bad markets returns only
   the good ones in ``keepers`` and the bad ones do not inflate
   ``markets_passing_gates``.

3. ``test_agent_config_default_slippage_is_100_bps`` — the new code
   default for ``max_hedge_slippage_bps`` is 100.0 (1%), not 200.0
   (2%). Verifies the fallback in ``AgentConfig.from_dict``.

These three tests prove the entire fix landed correctly. No tests for
unchanged scoring math, unchanged seed sizing, or unchanged auth.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from agent import AgentConfig
from discovery.market_quality import is_high_quality_resolution
from polymarket.discovery import discover_arb_candidates_with_stats


# ---------------------------------------------------------------------------
# Test 1 — accept/reject matrix


@pytest.mark.parametrize(
    "question,category,expected,why",
    [
        # ── accept: sports vs-match ──
        (
            "New York Yankees vs. New York Mets",
            "Sports",
            True,
            "MLB game — resolves on official final score",
        ),
        (
            "Spurs vs. Thunder",
            None,
            True,
            "NBA game — vs-pattern with no category should still accept",
        ),
        (
            "Will Arsenal FC win on 2026-05-18?",
            "Sports",
            True,
            "Single-team win question with date — EPL match, resolves cleanly",
        ),
        # ── accept: binary financial threshold ──
        (
            'Will "Michael" 4th Weekend Box Office be greater than 25m?',
            None,
            True,
            "Box office threshold — objective numeric resolution",
        ),
        (
            "Will BTC close above $100,000 on May 31?",
            "Crypto",
            True,
            "Price threshold with explicit dollar amount — resolves on official close",
        ),
        # ── accept: named election result ──
        (
            "Will Thomas Massie be the Republican nominee for KY-04?",
            "Politics",
            True,
            "Primary result — resolves on official tally",
        ),
        # ── reject: tweet-count buckets ──
        (
            "Will Elon Musk post 120-139 tweets from May 12 to May 19, 2026?",
            None,
            False,
            "Tweet-count bucket — sampling-window ambiguity, resolutions get disputed",
        ),
        (
            "Will Elon Musk post 400-419 tweets from May 15 to May 22, 2026?",
            None,
            False,
            "Another tweet-count bucket — same class, same risk",
        ),
        # ── reject: subjective political event ──
        (
            "Will Trump Insult Xi this week?",
            "Politics",
            False,
            "Subjective interpretation — 'insult' is opinion-dependent",
        ),
        (
            "Will Donald Trump announce that the United States blockade of the Strait of Hormuz has been lifted by May 22, 2026?",
            "Politics",
            False,
            "Announcement event — depends on news source interpretation",
        ),
        # ── reject: date-bounded geopolitical ──
        (
            "Iran closes its airspace by May 24?",
            None,
            False,
            "'Closes' undefined — civilian only? hourly? news-interpretation-dependent",
        ),
        (
            "Iran closes its airspace by May 21?",
            None,
            False,
            "Same pattern at different date — same ambiguity",
        ),
        # ── reject: default-deny on unrecognized pattern ──
        (
            "Will something completely unforeseen happen by Q3?",
            None,
            False,
            "Unknown pattern — default-deny is the economically correct policy",
        ),
    ],
)
def test_market_quality_filter_accept_reject_matrix(
    question: str, category: str | None, expected: bool, why: str
) -> None:
    """The filter must accept only objectively-resolvable markets.

    False positives (rejecting a real market) cost an opportunity;
    false negatives (accepting an ambiguous one) cost real seed money
    when the venues resolve differently. At the operator's typical
    $1-per-seed scale, default-deny is the right policy.
    """
    actual = is_high_quality_resolution(
        question=question, category=category, slug=None
    )
    assert actual is expected, (
        f"is_high_quality_resolution({question!r}, {category!r}) "
        f"returned {actual}, expected {expected}. Reason: {why}"
    )


# ---------------------------------------------------------------------------
# Test 2 — integration: discover_arb_candidates_with_stats applies the filter


NOW_TS = datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 5, 24, 23, 59, 59, tzinfo=timezone.utc)


def _gamma_row(
    *,
    condition_id: str,
    question: str,
    volume_24h: float = 100_000.0,
    end_offset_hours: float = 72.0,
    category: str | None = "Sports",
) -> dict:
    """Mirror the live Polymarket Gamma row shape closely enough that
    the discovery filter treats fixture rows the same way it treats
    production rows."""
    end_dt = NOW_TS + timedelta(hours=end_offset_hours)
    return {
        "conditionId": condition_id,
        "question": question,
        "endDate": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "volume24hr": volume_24h,
        "category": category,
        "closed": False,
        "active": True,
        # #631 — clobTokenIds is required so the downstream token_id
        # plumbing also stays green. Two distinct uint256 decimals.
        "clobTokenIds": [f"{condition_id}_YES", f"{condition_id}_NO"],
    }


class _StubGateway:
    """Returns a canned Gamma `/markets` response."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def call(self, publisher: str, method: str, path: str, body: Any = None) -> Any:
        return list(self.rows)


def test_auto_discover_excludes_low_quality_markets() -> None:
    """A mixed fixture of high- and low-quality markets must produce a
    keepers list containing only the high-quality ones, and the
    ``markets_passing_gates`` counter must reflect only the survivors.

    Pre-fix this returned all 5 candidates including the tweet-count
    bucket and the airspace event — both of which would seed $2 of
    operator capital into markets with high resolution-mismatch risk.
    """
    rows = [
        # GOOD — keep
        _gamma_row(
            condition_id="cond_yankees_mets",
            question="New York Yankees vs. New York Mets",
            volume_24h=1_100_000.0,
        ),
        _gamma_row(
            condition_id="cond_box_office",
            question='Will "Michael" 4th Weekend Box Office be greater than 25m?',
            volume_24h=45_000.0,
            category=None,
        ),
        # BAD — drop (tweet-count bucket)
        _gamma_row(
            condition_id="cond_elon_tweets",
            question="Will Elon Musk post 120-139 tweets from May 12 to May 19, 2026?",
            volume_24h=280_000.0,
            category=None,
        ),
        # BAD — drop (subjective political event)
        _gamma_row(
            condition_id="cond_trump_insult",
            question="Will Trump Insult Xi this week?",
            volume_24h=80_000.0,
            category="Politics",
        ),
        # BAD — drop (date-bounded geopolitical)
        _gamma_row(
            condition_id="cond_iran_airspace",
            question="Iran closes its airspace by May 24?",
            volume_24h=123_000.0,
            category=None,
        ),
    ]

    candidates, stats = discover_arb_candidates_with_stats(
        gateway=_StubGateway(rows),
        deadline=DEADLINE,
        min_24h_volume_usd=10_000.0,
        minimum_headroom_seconds=24 * 3600,
        max_candidates=250,
        now=NOW_TS,
    )

    kept_ids = [c.polymarket_market_id for c in candidates]
    assert kept_ids == ["cond_yankees_mets", "cond_box_office"], (
        "Filter must keep only sports + binary financial; reject tweet-count, "
        "subjective political, and date-bounded geopolitical markets."
    )

    # The counter must reflect the post-filter universe — low-quality
    # markets that the bot would never want to pair must NOT inflate
    # this number, otherwise the operator's run summary will show
    # phantom "addressable" candidates that auto-discover ignores.
    assert stats.markets_passing_gates == 2

    # `raw_markets_fetched` is the pre-filter input count; it stays at 5
    # so the operator can still see how many low-quality rows the
    # publisher returned this cycle.
    assert stats.raw_markets_fetched == 5


# ---------------------------------------------------------------------------
# Test 3 — AgentConfig default slippage is 100.0


def test_agent_config_default_slippage_is_100_bps() -> None:
    """When `max_hedge_slippage_bps` is missing from the config dict,
    ``AgentConfig.from_dict`` must default to 100.0 (1%), not 200.0 (2%).

    A 2% slippage cap on thin Prophet books eats most of the 3¢
    quoted-spread floor before the position is even held. Lower the
    default so new operators picking up `config.example.json` start at
    the right risk posture; existing configs that explicitly set 200.0
    are not silently mutated.
    """
    raw = {
        "inputs": {"prophet_email": "test@example.com"},
        "storage": {"project_name": "prophet", "database_name": "prophet"},
        "scoring": {
            "min_spread": 0.03,
            "max_spread": 0.30,
            "kelly_fraction": 0.25,
            "max_trade_size_usdc": 50.0,
            "min_trade_size_usdc": 5.0,
            "bankroll_usdc": 200.0,
        },
        "intelligence": {
            "enabled": False,
            "max_basis_volatility": 0.05,
            "fetch_correlations": True,
        },
        "auto_discover": {"enabled": False},
        "live_mode": False,
        "max_orders_per_run": 5,
        "execution_mode": "delta_neutral",
        # max_hedge_slippage_bps intentionally omitted — must default to 100.0.
    }
    config = AgentConfig.from_dict(raw)
    assert config.max_hedge_slippage_bps == 100.0, (
        "Default slippage cap must be 1% (100 bps) — 2% (200 bps) is too "
        "loose for the thin Prophet books the bot quotes against."
    )
