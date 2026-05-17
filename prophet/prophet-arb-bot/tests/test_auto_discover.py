"""Auto-discover module (#538).

When invoked with `auto_discover.enabled=true`, the arb-bot must:

  1. Filter live Polymarket candidates by 24h volume and the
     execution-headroom floor before any other work.
  2. Look up matching Prophet markets via `markets_for_dedup` and
     auto-pair the matches.
  3. Emit `pending_ui_submission` entries for unmatched candidates with
     the **exact** envelope shape the bounty-runner uses, so the
     agent's `/create` Playwright runbook works without branching on
     skill identity.
  4. Skip candidates already present in `arb_pairs` so the bot doesn't
     double-queue UI work.

These four behaviors are the entire auto-discover surface. Everything
else (scoring, hedge submission, persistence transactions) has its
own test file.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from discovery.auto_discover import (
    AutoDiscoverConfig,
    _build_pending_entry,
    run_auto_discover,
)
from discovery.seed_qualifier import qualify_and_trim_pending
from discovery.prophet_pair_lookup import (
    _normalize_question,
    find_matching_prophet_markets,
)
from polymarket.discovery import PolymarketSource, discover_arb_candidates


NOW_TS = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 5, 24, 23, 59, 59, tzinfo=timezone.utc)


def _row(
    *,
    condition_id: str,
    question: str,
    volume_24h: float,
    end_offset_hours: float,
    category: str = "Sports",
    slug: str | None = None,
    closed: bool = False,
) -> dict:
    """Build a Gamma `/markets` row close enough to the live shape that
    the discovery filter treats it the same way it treats production."""
    end_dt = NOW_TS + timedelta(hours=end_offset_hours)
    return {
        "conditionId": condition_id,
        "question": question,
        "endDate": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "volume24hr": volume_24h,
        "category": category,
        "closed": closed,
        "active": True,
        "slug": slug,
        # #631: Polymarket Gamma always returns clobTokenIds on active
        # markets ([YES_token_id, NO_token_id], both uint256 decimal).
        # The auto-discover pipeline pipes index 0 (YES) through to the
        # pending entry so the seed_qualifier can probe Polymarket book
        # depth. Mirror the field here so test fixtures match production.
        "clobTokenIds": [f"{condition_id}_YES", f"{condition_id}_NO"],
    }


class _StubGateway:
    """Returns a canned Gamma `/markets` response."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str, str]] = []

    def call(self, publisher: str, method: str, path: str, body=None):
        self.calls.append((publisher, method, path))
        return list(self.rows)


# ---------------------------------------------------------------------------
# 1. Filtering — volume + headroom + deadline


def test_discover_filters_low_volume_and_no_headroom_and_post_deadline() -> None:
    """Three negative filters in one assertion sweep, because they
    share the same Gamma-row scaffolding.

    Each rejection reason is documented in the row's question so a
    test failure points at the gate that broke.
    """
    rows = [
        _row(
            condition_id="cond_low_vol",
            question="Reject: vol $500 below $10k floor",
            volume_24h=500.0,
            end_offset_hours=72,
        ),
        _row(
            condition_id="cond_no_headroom",
            question="Reject: resolves in 1h, inside 24h floor",
            volume_24h=50_000.0,
            end_offset_hours=1,
        ),
        _row(
            condition_id="cond_past_deadline",
            question="Reject: resolves after 2026-05-24",
            volume_24h=50_000.0,
            end_offset_hours=24 * 30,
        ),
        _row(
            condition_id="cond_ok",
            question="Keep: $50k vol, resolves in 3 days",
            volume_24h=50_000.0,
            end_offset_hours=72,
            slug="keep-market",
        ),
    ]
    gateway = _StubGateway(rows)

    out = discover_arb_candidates(
        gateway=gateway,
        deadline=DEADLINE,
        min_24h_volume_usd=10_000.0,
        minimum_headroom_seconds=24 * 3600,
        max_candidates=50,
        now=NOW_TS,
    )

    assert [c.polymarket_market_id for c in out] == ["cond_ok"]
    assert out[0].volume_24h_usd == pytest.approx(50_000.0)
    assert out[0].slug == "keep-market"


def test_discover_caps_at_max_candidates() -> None:
    """The campaign batch is small (~24 markets); the cap is a safety
    net so a noisy Gamma response can't blow up the agent's UI queue."""
    rows = [
        _row(
            condition_id=f"cond_{i}",
            question=f"Will event {i} resolve?",
            volume_24h=20_000.0 + i,
            end_offset_hours=72,
        )
        for i in range(60)
    ]
    out = discover_arb_candidates(
        gateway=_StubGateway(rows),
        deadline=DEADLINE,
        min_24h_volume_usd=10_000.0,
        minimum_headroom_seconds=24 * 3600,
        max_candidates=10,
        now=NOW_TS,
    )
    assert len(out) == 10


def test_default_auto_discover_checks_later_candidates_before_no_pending_work(
    tmp_path,
) -> None:
    """Regression for #627.

    The first 50 candidates can all fail seed hedge preflight. The
    default auto-discover window must still include later candidates so
    a hedge-qualified row at position 51 is not missed.
    """
    rows = [
        _row(
            condition_id=f"cond_{i:03d}",
            question=f"Will event {i} resolve?",
            volume_24h=100_000.0 - i,
            end_offset_hours=72,
        )
        for i in range(60)
    ]

    class _NoProphetMatches:
        def markets_for_dedup(self, *, jwt, limit):
            return []

    result = run_auto_discover(
        gateway=_StubGateway(rows),
        prophet_client=_NoProphetMatches(),
        jwt="eyJ.fake.jwt",
        target=None,
        config=AutoDiscoverConfig(
            enabled=True,
            min_24h_volume_usd=10_000.0,
            min_headroom_hours=24.0,
            resolution_deadline_iso=DEADLINE.isoformat(),
        ),
        sheet_output_dir=tmp_path,
        now=NOW_TS,
    )

    assert result.raw_markets_fetched == 60
    assert result.markets_passing_gates == 60
    assert result.candidates_found == 60
    assert result.candidates_evaluated_for_pairing == 60

    def first_50_fail(token_id: str, size_usdc: float, max_slippage_bps: float) -> bool:
        # #631: the qualifier feeds the YES token_id, not condition_id.
        # _row(...) sets clobTokenIds=[f"{cid}_YES", f"{cid}_NO"].
        return token_id == "cond_050_YES"

    decision = qualify_and_trim_pending(
        pending=result.pending_ui_submission,
        max_fundable_count=1,
        initial_bet_usdc=1.0,
        depth_assessor=first_50_fail,
        max_hedge_slippage_bps=200.0,
    )

    assert [e["polymarket_market_id"] for e in decision.qualified] == ["cond_050"]


# ---------------------------------------------------------------------------
# 2. Prophet pair lookup


def test_prophet_pair_lookup_matches_on_normalized_substring() -> None:
    """The matcher must tolerate punctuation/casing drift on
    near-identical question text. Production case: Prophet's `/create`
    AI copies Jill's spreadsheet question with minor capitalization or
    punctuation differences. The matcher should still pair, while
    rejecting unrelated markets in the same Prophet inventory.
    """

    class _StubProphetClient:
        def markets_for_dedup(self, *, jwt, limit):
            return [
                # Near-identical to the Polymarket question — casing
                # and punctuation drift only, same words.
                {
                    "id": "PRO-001",
                    "question": "new york yankees vs baltimore orioles!!",
                },
                # Totally unrelated row that must not match.
                {"id": "PRO-002", "question": "Will Bitcoin hit $200k by year end?"},
            ]

    matched = find_matching_prophet_markets(
        prophet_client=_StubProphetClient(),
        jwt="eyJ.fake.jwt",
        candidate_questions={
            "cond_yankees": "New York Yankees vs. Baltimore Orioles",
            "cond_btc_50k": "Will BTC hit $50k by August?",
        },
    )

    assert matched == {"cond_yankees": "PRO-001"}


def test_normalize_question_strips_punctuation_and_collapses_whitespace() -> None:
    """Direct test of the normalizer because it's the entire matching
    surface — a regression here silently disables auto-pairing."""
    assert _normalize_question("Will  the   Yankees beat?!!  ") == "will the yankees beat"
    assert _normalize_question("") == ""


# ---------------------------------------------------------------------------
# 3. `pending_ui_submission` envelope shape


def test_pending_ui_submission_entry_matches_bounty_runner_shape() -> None:
    """Field-for-field check against the bounty-runner's envelope so
    the agent's `/create` Playwright runbook treats both skills'
    output identically. Drift here breaks the runbook silently —
    the agent would still drive the UI but capture the wrong skill's
    record-back path.

    Issue #631: the entry MUST carry `polymarket_yes_token_id` alongside
    `polymarket_market_id`. The seed_qualifier's depth_assessor calls
    Polymarket CLOB's `/book?token_id=...` which requires the uint256
    decimal token_id, not the hex condition_id. Without this field the
    depth probe returns empty and every candidate fails as
    `hedge_ineligible` (proven blast radius: 100% of auto-discover
    seed candidates rejected in live runs).
    """
    cand = PolymarketSource(
        polymarket_market_id="cond_abc123",
        polymarket_yes_token_id="9876543210123456789",
        question="Will the Knicks win Game 5?",
        resolution_date=datetime(2026, 5, 19, 22, 0, 0, tzinfo=timezone.utc),
        category="Sports",
        settled=False,
        volume_24h_usd=125_000.0,
        slug="knicks-game-5",
    )

    entry = _build_pending_entry(cand=cand, initial_bet_usdc=1.0, viewer_id="vid_42")

    # The exact eight fields the bounty-runner emits (audited 2026-05-14)
    # plus `source_skill` that lets the agent route the captured
    # prophet_market_id back to the right skill's persistence, plus
    # `polymarket_yes_token_id` (#631) that lets the seed_qualifier
    # probe Polymarket book depth without a separate runtime lookup.
    assert set(entry.keys()) == {
        "polymarket_market_id",
        "polymarket_yes_token_id",
        "question",
        "category",
        "category_slug",
        "resolution_date_iso",
        "initial_bet_usdc",
        "bounty_id",
        "prophet_viewer_id",
        "source_skill",
    }
    assert entry["polymarket_market_id"] == "cond_abc123"
    assert entry["polymarket_yes_token_id"] == "9876543210123456789"
    assert entry["category_slug"] == "sports"
    assert entry["initial_bet_usdc"] == 1.0
    assert entry["prophet_viewer_id"] == "vid_42"
    assert entry["source_skill"] == "prophet-arb-bot"
    # bounty_id is empty because the arb-bot doesn't carry a bounty.
    assert entry["bounty_id"] == ""


# ---------------------------------------------------------------------------
# 4. Orchestrator dedup + auto-pair + pending split


def test_run_auto_discover_dedups_existing_pairs_and_splits_matches() -> None:
    """End-to-end: feed a candidate set where one is already paired,
    one matches a live Prophet market, and one needs UI creation.
    Assert the three paths fan out correctly.
    """
    rows = [
        _row(
            condition_id="cond_already",
            question="Already-paired market",
            volume_24h=80_000.0,
            end_offset_hours=72,
        ),
        _row(
            condition_id="cond_match",
            question="New York Yankees vs. Baltimore Orioles",
            volume_24h=150_000.0,
            end_offset_hours=72,
        ),
        _row(
            condition_id="cond_pending",
            question="Will Greece win Eurovision 2026?",
            volume_24h=350_000.0,
            end_offset_hours=72,
        ),
    ]

    upsert_calls: list[dict] = []
    list_calls: list[None] = []

    class _StubTarget:
        # Just a sentinel; persistence is stubbed below.
        pass

    target_sentinel = _StubTarget()

    # Monkey-patch the persistence functions the orchestrator imports
    # at module load. We import locally so we can swap them in-place.
    from discovery import auto_discover as ad_module

    def _fake_list_pairs(*, target):
        list_calls.append(None)
        # First call → existing pair includes cond_already. Subsequent
        # calls (after upsert) include the new cond_match auto_pair.
        if len(list_calls) == 1:
            return [{"prophet_market_id": "PRO-OLD", "polymarket_condition_id": "cond_already"}]
        return [
            {"prophet_market_id": "PRO-OLD", "polymarket_condition_id": "cond_already"},
            {"prophet_market_id": "PRO-NEW", "polymarket_condition_id": "cond_match"},
        ]

    def _fake_upsert(*, target, prophet_market_id, polymarket_condition_id, source_skill):
        upsert_calls.append(
            {
                "prophet_market_id": prophet_market_id,
                "polymarket_condition_id": polymarket_condition_id,
                "source_skill": source_skill,
            }
        )

    # The sheet writer touches disk — short-circuit it.
    def _fake_sheet(**_kwargs):
        return None

    monkeypatch_module = pytest.MonkeyPatch()
    try:
        monkeypatch_module.setattr(ad_module, "list_arb_pairs", _fake_list_pairs)
        monkeypatch_module.setattr(ad_module, "upsert_arb_pair", _fake_upsert)
        monkeypatch_module.setattr(ad_module, "write_candidate_sheet", _fake_sheet)

        class _StubProphetClient:
            def markets_for_dedup(self, *, jwt, limit):
                return [
                    {
                        "id": "PRO-NEW",
                        "question": "new york yankees vs baltimore orioles!!",
                    },
                ]

        result = ad_module.run_auto_discover(
            gateway=_StubGateway(rows),
            prophet_client=_StubProphetClient(),
            jwt="eyJ.fake.jwt",
            target=target_sentinel,
            config=AutoDiscoverConfig(
                enabled=True,
                min_24h_volume_usd=10_000.0,
                min_headroom_hours=24.0,
                resolution_deadline_iso=DEADLINE.isoformat(),
                max_candidates=10,
            ),
            now=NOW_TS,
        )
    finally:
        monkeypatch_module.undo()

    assert result.candidates_found == 3
    assert result.already_paired == 1  # cond_already already in arb_pairs
    assert len(result.auto_paired) == 1  # cond_match matched a live Prophet market
    assert result.auto_paired[0]["polymarket_condition_id"] == "cond_match"
    assert result.auto_paired[0]["prophet_market_id"] == "PRO-NEW"
    assert len(result.pending_ui_submission) == 1  # cond_pending needs UI creation
    assert (
        result.pending_ui_submission[0]["polymarket_market_id"] == "cond_pending"
    )
    # Persistence side effect — the matched pair was upserted with the
    # correct source_skill so the operator can tell auto vs manual rows.
    assert upsert_calls == [
        {
            "prophet_market_id": "PRO-NEW",
            "polymarket_condition_id": "cond_match",
            "source_skill": "auto_discover",
        }
    ]


# ---------------------------------------------------------------------------
# 5. Diagnostic propagation (#611)


def test_run_auto_discover_surfaces_prophet_lookup_exception_detail() -> None:
    """When `find_matching_prophet_markets` raises, the orchestrator
    must continue gracefully (existing soft-fail contract) AND surface
    the exception class + message so the operator can triage. Pre-#611
    the boolean `prophet_lookup_failed` was the only signal; the
    underlying exception was silently dropped.

    This is the only test for the diagnostic-capture seam. Behavior on
    the non-exceptional path is already covered by
    `test_run_auto_discover_dedups_existing_pairs_and_splits_matches`.
    """

    class _RaisingProphetClient:
        def markets_for_dedup(self, *, jwt, limit):
            raise RuntimeError("simulated viewer.markets timeout")

    rows = [
        _row(
            condition_id="cond_pending_after_lookup_failure",
            question="Will the lookup fail and surface?",
            volume_24h=80_000.0,
            end_offset_hours=72,
        ),
    ]

    from discovery import auto_discover as ad_module

    monkeypatch_module = pytest.MonkeyPatch()
    try:
        monkeypatch_module.setattr(ad_module, "list_arb_pairs", lambda *, target: [])
        monkeypatch_module.setattr(ad_module, "write_candidate_sheet", lambda **_: None)

        result = ad_module.run_auto_discover(
            gateway=_StubGateway(rows),
            prophet_client=_RaisingProphetClient(),
            jwt="eyJ.fake.jwt",
            target=None,
            config=AutoDiscoverConfig(
                enabled=True,
                min_24h_volume_usd=10_000.0,
                min_headroom_hours=24.0,
                resolution_deadline_iso=DEADLINE.isoformat(),
                max_candidates=10,
            ),
            now=NOW_TS,
        )
    finally:
        monkeypatch_module.undo()

    # Existing soft-fail behavior — cycle continues, candidate falls to
    # pending_ui_submission. Must not regress.
    assert result.prophet_lookup_failed is True
    assert len(result.pending_ui_submission) == 1

    # The bug fix: exception class AND message both reach the result.
    # Operator now sees what failed, not just "something failed".
    assert result.prophet_failure_detail is not None
    assert "RuntimeError" in result.prophet_failure_detail
    assert "simulated viewer.markets timeout" in result.prophet_failure_detail
