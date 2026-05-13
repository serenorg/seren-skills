"""Phase 15 (#505) — Playwright pivot regression test.

Live audit (2026-05-13) found that `createMarketWithBet` requires an
in-browser Privy signing prompt that no agent-accessible API can
drive. Phase 15 replaces the four-step chain submission with a
handoff: `_cmd_run` stops at the dedup pre-filter and emits
`pending_ui_submission` so the agent (Seren Desktop's
`mcp__playwright__*` runbook) finalizes each market via the Prophet
`/create` UI.

ONE critical assertion suffices: after a successful run, the envelope
returns `status=ok`, `prophet_markets_created` is empty,
`pending_ui_submission` carries the surviving candidates, and the
storage events table records one
`prophet.market_pending_ui_submission` row per pending entry.

This file replaces:
  - `test_phase14_chain_migration.py` (the chain is no longer called)
  - `test_phase14b_dedup_prefilter.py` (the dedup pre-filter is still
    in place — its output feeds into pending_ui_submission instead of
    the chain, which this test exercises end-to-end)
  - the chain-dependent assertion in `test_persistence.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent import run_command  # noqa: E402

from conftest import load_fixture  # type: ignore[import-not-found]


def _seed_polymarket_and_bounty(stub_gateway) -> None:
    """Register the non-Prophet calls the run depends on.

    Phase 15: the polymarket-data URL pulls the bumped 500-market
    sample and is pinned to the May-24 deadline that the Prophet UI
    enforces.
    """
    stub_gateway.register(
        "seren-bounty",
        "POST",
        "/bounties/bounty_fixture_001/join",
        load_fixture("bounty_join.json"),
    )
    stub_gateway.register(
        "polymarket-data",
        "GET",
        (
            "/markets?end_date_min=2026-05-08T00:00:00Z"
            "&end_date_max=2026-05-24T00:00:00Z"
            "&closed=false&active=true&order=endDate&ascending=true&limit=500"
        ),
        load_fixture("polymarket_settling.json"),
    )


def test_run_emits_pending_ui_submission_after_dedup(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    """`_cmd_run` after Phase 15 stops at dedup and hands off to the agent."""
    monkeypatch.setattr(
        "agent.acquire_prophet_token_via_otp",
        lambda *_args, **_kw: load_fixture("prophet_otp_session.json"),
    )
    # Pin discovery's "now" so the end_date_min URL parameter matches
    # the path registered in `_seed_polymarket_and_bounty`.
    monkeypatch.setattr(
        "agent._get_now",
        lambda: datetime(2026, 5, 8, 0, 0, 0, tzinfo=timezone.utc),
    )
    _seed_polymarket_and_bounty(stub_gateway)
    # Dedup pre-filter is still in place — register an empty edges
    # response so every discovered candidate flows into
    # pending_ui_submission.
    stub_transport.register(
        "MarketsForDedup",
        {"data": {"markets": {"edges": []}}},
    )
    # #524 funds preflight runs between dedup and the pending-emit
    # loop. Seed a comfortably-funded balance so this Phase-15 test
    # stays focused on the dedup -> pending handoff and doesn't
    # double as a funds-preflight test.
    stub_transport.register(
        "ViewerWalletBalance",
        {"data": {"viewer": {"cashBalance": {"availableCents": 10000, "totalCents": 10000}}}},
    )

    result = run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    assert result["status"] == "ok"
    assert result["prophet_markets_created"] == []
    pending = result["pending_ui_submission"]
    assert len(pending) >= 1
    assert len(pending) <= base_run_request["submit_limit"]
    # Every pending entry carries enough context for the agent to
    # drive the Prophet `/create` UI without re-fetching from
    # polymarket-data.
    for entry in pending:
        assert entry["polymarket_market_id"]
        assert entry["question"]
        assert entry["resolution_date_iso"]
        assert entry["initial_bet_usdc"] == 1
        assert entry["bounty_id"] == "bounty_fixture_001"

    # One persisted event per pending entry — operator's reconciler
    # uses this to know what the agent is about to drive in the UI.
    pending_events = [
        e for e in stub_storage.events
        if e.get("event_type") == "prophet.market_pending_ui_submission"
    ]
    assert len(pending_events) == len(pending)

    # No `markets_created` rows yet — those land after the agent
    # drives the UI and reports back via `record_created_market`.
    assert stub_storage.markets_created == []

    # Run row reflects the handoff: status=succeeded, market_count=0.
    assert len(stub_storage.runs) == 1
    run_row = stub_storage.runs[0]
    assert run_row["status"] == "succeeded"
    assert run_row["market_count"] == 0

    # The chain is no longer invoked from Python: no
    # `InitiateMarket` / `StartOddsCalculation` /
    # `CreateMarketWithBet` operations should appear in the transport
    # call log. Only the dedup pre-filter (`MarketsForDedup`) fires.
    chain_op_names = {
        "InitiateMarket",
        "StartOddsCalculation",
        "OddsCalculationSession",
        "MarketCreationOrderParams",
        "CreateMarketWithBet",
    }
    transport_ops = {c.get("operation_name") for c in stub_transport.calls}
    assert transport_ops & chain_op_names == set()
