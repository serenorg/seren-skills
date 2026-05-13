"""Phase-14 chain migration regression tests (issue #505).

Three critical assertions for the migration from the obsolete
single-shot `createMarket(source: PolymarketSourceInput!)` mutation to
the four-step chain implemented in
`MinimalProphetClient.create_market_chain`:

  1. `_cmd_run` invokes the chain (operation_name=InitiateMarket appears
     in transport.calls) — and does NOT issue the obsolete CreateMarket
     mutation. This is the load-bearing wiring check; without it the
     migration is incomplete.

  2. When any step of the chain raises `ProphetSchemaError`, the run
     envelope downgrades to `status=blocked` with
     `reason=prophet_schema_drift` and the error is surfaced into
     `blockers[]`. This is the fail-closed UX the issue #505 acceptance
     criteria mandate — silent swallowing into the events table is the
     bug being fixed.

  3. When the chain completes successfully, the run envelope returns
     `status=ok` and the market is persisted via the post-create
     re-fetch.

The chain's GraphQL input shapes remain best-guess until the live
`schema_probe.py` runs against a fresh Privy JWT — this PR fixes the
*wiring* and *fail-closed UX*, not the schema field names. Pinning the
shapes against an introspected fixture is the follow-on per #505.
"""

from __future__ import annotations

import pytest

from agent import run_command  # noqa: E402

from conftest import (  # type: ignore[import-not-found]
    load_fixture,
    seed_prophet_chain_happy_path,
)

from prophet import ProphetSchemaError  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers


def _seed_polymarket_and_bounty(stub_gateway) -> None:
    """Register the non-Prophet calls the run depends on."""
    stub_gateway.register(
        "seren-bounty",
        "POST",
        "/bounties/bounty_fixture_001/join",
        load_fixture("bounty_join.json"),
    )
    stub_gateway.register(
        "polymarket-data",
        "GET",
        "/markets?end_date_max=2026-05-26T00:00:00Z&closed=false&active=true&limit=100",
        load_fixture("polymarket_settling.json"),
    )
    stub_gateway.register(
        "seren-bounty",
        "POST",
        "/bounties/bounty_fixture_001/submission",
        {"submission_id": "sub_fixture_001", "status": "submitted"},
    )


# ---------------------------------------------------------------------------
# Critical test 1: chain is the call path, not the obsolete mutation


def test_cmd_run_invokes_chain_not_obsolete_create_market(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    """The migration is wired through: InitiateMarket fires; CreateMarket
    (the obsolete single-shot mutation) never does."""
    monkeypatch.setattr(
        "agent.acquire_prophet_token_via_otp",
        lambda *_args, **_kw: load_fixture("prophet_otp_session.json"),
    )
    _seed_polymarket_and_bounty(stub_gateway)
    seed_prophet_chain_happy_path(stub_transport)
    request = {**base_run_request, "submit_limit": 1}

    result = run_command(
        request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    op_names = [c.get("operation_name") for c in stub_transport.calls]
    assert "InitiateMarket" in op_names, (
        f"Phase 14 chain not wired — InitiateMarket missing from {op_names}"
    )
    assert "CreateMarket" not in op_names, (
        f"Obsolete CreateMarket mutation still issued: {op_names}"
    )
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Critical test 2: chain schema error → status=blocked + blockers[] populated


def test_cmd_run_chain_schema_error_blocks_run_and_populates_blockers(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    """A ProphetSchemaError on any chain step downgrades the envelope to
    `status=blocked` with `reason=prophet_schema_drift` and surfaces the
    error to `blockers[]`. No silent swallow; the cron's auto-pause path
    needs this signal to stop firing on a known-broken schema."""
    monkeypatch.setattr(
        "agent.acquire_prophet_token_via_otp",
        lambda *_args, **_kw: load_fixture("prophet_otp_session.json"),
    )
    _seed_polymarket_and_bounty(stub_gateway)
    stub_transport.register(
        "InitiateMarket",
        ProphetSchemaError("InitiateMarketInput unknown — schema drift"),
    )

    result = run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "prophet_schema_drift"
    assert isinstance(result.get("blockers"), list) and result["blockers"], (
        f"blockers[] missing or empty: {result.get('blockers')!r}"
    )
    assert any("InitiateMarketInput" in b for b in result["blockers"]), (
        f"chain error not surfaced into blockers: {result['blockers']!r}"
    )
    assert result["prophet_markets_created"] == []


# ---------------------------------------------------------------------------
# Critical test 3: chain success → market persisted with correct viewer binding


def test_cmd_run_chain_success_persists_market_record(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    """Happy-path chain returns the new market_id and the agent persists
    one `markets_created` row tied to the bounty and viewer."""
    monkeypatch.setattr(
        "agent.acquire_prophet_token_via_otp",
        lambda *_args, **_kw: load_fixture("prophet_otp_session.json"),
    )
    _seed_polymarket_and_bounty(stub_gateway)
    seed_prophet_chain_happy_path(stub_transport)
    request = {**base_run_request, "submit_limit": 1}

    result = run_command(
        request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    assert result["status"] == "ok"
    assert len(result["prophet_markets_created"]) == 1
    assert (
        result["prophet_markets_created"][0]["prophet_market_id"]
        == "prophet_market_fixture_001"
    )
    persisted = [
        r
        for r in stub_storage.markets_created
        if r.get("prophet_market_id") == "prophet_market_fixture_001"
    ]
    assert len(persisted) == 1
    assert persisted[0]["bounty_id"] == "bounty_fixture_001"
    assert persisted[0]["prophet_viewer_id"] == "viewer_fixture_001"
