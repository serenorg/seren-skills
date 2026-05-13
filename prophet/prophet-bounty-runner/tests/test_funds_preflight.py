"""Funds preflight (#524) — Phase 15 follow-on.

Issue #524: the Phase-15 UI submission runbook debits
`DEFAULT_INITIAL_BET_USDC` from Prophet's protocol cash at the bet-form
confirm step. Before this preflight existed, `_cmd_run` emitted
`pending_ui_submission` candidates with zero regard for whether the
operator had cash to fund them — every cron tick on an unfunded wallet
burned 90-180s of agent compute per candidate (Validate + odds calc)
only to fail at the bet form.

Two critical-path tests cover the fix:

  1. Funds-insufficient → blocked envelope, `action=deposit_required`,
     `pending_ui_submission == []`, persisted event row.
  2. Funds-sufficient → ok envelope, `pending_ui_submission` populated
     as before, no `funds_insufficient_for_seed_bets` event.

A third test pins the wire-shape of `MinimalProphetClient.cash_balance`
so the GraphQL operation name (`ViewerWalletBalance`) stays in sync with
the sibling skills that already use it (prophet-adversarial-auditor,
prophet-arb-bot).
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent import run_command
from prophet.client import MinimalProphetClient, ViewerCashBalance

from conftest import load_fixture  # type: ignore[import-not-found]


def _seed_polymarket_and_bounty(stub_gateway) -> None:
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


def _seed_prophet_dedup_empty(stub_transport) -> None:
    stub_transport.register(
        "MarketsForDedup",
        {"data": {"markets": {"edges": []}}},
    )


def _seed_cash_balance(stub_transport, *, available_cents: int, total_cents: int | None = None) -> None:
    stub_transport.register(
        "ViewerWalletBalance",
        {
            "data": {
                "viewer": {
                    "cashBalance": {
                        "availableCents": available_cents,
                        "totalCents": total_cents if total_cents is not None else available_cents,
                    }
                }
            }
        },
    )


def test_cash_balance_decodes_viewer_wallet_balance_payload(stub_transport) -> None:
    """Pin the GraphQL operation name and the response decode shape.

    Sibling skill prophet-adversarial-auditor declares the operation as
    `ViewerWalletBalance` in its SKILL.md auth contract. Drifting the
    name here would silently break cross-skill schema fixtures.
    """
    stub_transport.register(
        "ViewerWalletBalance",
        {"data": {"viewer": {"cashBalance": {"availableCents": 1234, "totalCents": 5678}}}},
    )
    client = MinimalProphetClient(transport=stub_transport)

    balance = client.cash_balance(jwt="eyJ.fake.jwt")

    assert isinstance(balance, ViewerCashBalance)
    assert balance.available_cents == 1234
    assert balance.total_cents == 5678
    assert balance.available_usdc == 12.34
    # Confirm operation_name actually rode through to the transport so
    # future schema_probe captures match what we send in production.
    call = next(c for c in stub_transport.calls if c["operation_name"] == "ViewerWalletBalance")
    assert "ViewerWalletBalance" in call["query"]


def test_run_blocks_with_deposit_required_when_funds_insufficient(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    """Insufficient protocol cash → blocked envelope, zero UI handoff."""
    monkeypatch.setattr(
        "agent.acquire_prophet_token_via_otp",
        lambda *_args, **_kw: load_fixture("prophet_otp_session.json"),
    )
    monkeypatch.setattr(
        "agent._get_now",
        lambda: datetime(2026, 5, 8, 0, 0, 0, tzinfo=timezone.utc),
    )
    _seed_polymarket_and_bounty(stub_gateway)
    _seed_prophet_dedup_empty(stub_transport)
    # submit_limit=3, DEFAULT_INITIAL_BET_USDC=1 → need 3 USDC. Wallet has 0.50.
    _seed_cash_balance(stub_transport, available_cents=50, total_cents=50)

    result = run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "funds_insufficient"
    assert result["action"] == "deposit_required"
    deposit = result["deposit"]
    assert deposit["chain"] == "polygon"
    assert deposit["chain_id"] == 137
    assert deposit["available_usdc"] == 0.50
    assert deposit["needed_usdc"] >= 1
    assert deposit["deficit_usdc"] >= 0.50
    # Agent-actionable: surface the contract so the deposit runbook can
    # query on-chain USDC via the seren-polygon publisher without a
    # second introspection step.
    assert deposit["usdc_contract_polygon"] == "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"

    # No agent work emitted — this is the whole point of the preflight.
    assert "pending_ui_submission" not in result or result["pending_ui_submission"] == []
    # No markets_created (they're never created in Python after Phase 15
    # anyway, but assert explicitly so a regression here is loud).
    assert stub_storage.markets_created == []

    # Persisted event so the operator's reconciler / dashboard can see
    # the gap without re-running the cycle.
    funds_events = [
        e for e in stub_storage.events
        if e.get("event_type") == "prophet.funds_insufficient_for_seed_bets"
    ]
    assert len(funds_events) == 1
    assert funds_events[0]["available_usdc"] == 0.50
    assert funds_events[0]["deficit_usdc"] >= 0.50

    # Run row reflects the block so cron telemetry can attribute it.
    assert len(stub_storage.runs) == 1
    assert stub_storage.runs[0]["status"] == "blocked_funds_insufficient"


def test_run_proceeds_when_funds_sufficient(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    """Sufficient protocol cash → Phase-15 pending_ui_submission as before."""
    monkeypatch.setattr(
        "agent.acquire_prophet_token_via_otp",
        lambda *_args, **_kw: load_fixture("prophet_otp_session.json"),
    )
    monkeypatch.setattr(
        "agent._get_now",
        lambda: datetime(2026, 5, 8, 0, 0, 0, tzinfo=timezone.utc),
    )
    _seed_polymarket_and_bounty(stub_gateway)
    _seed_prophet_dedup_empty(stub_transport)
    # 10 USDC available — comfortably above the 3 USDC the run would need.
    _seed_cash_balance(stub_transport, available_cents=1000, total_cents=1000)

    result = run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    assert result["status"] == "ok"
    pending = result["pending_ui_submission"]
    assert len(pending) >= 1
    assert len(pending) <= base_run_request["submit_limit"]

    # No funds-insufficient event should be persisted on the happy path.
    funds_events = [
        e for e in stub_storage.events
        if e.get("event_type") == "prophet.funds_insufficient_for_seed_bets"
    ]
    assert funds_events == []

    assert len(stub_storage.runs) == 1
    assert stub_storage.runs[0]["status"] == "succeeded"
