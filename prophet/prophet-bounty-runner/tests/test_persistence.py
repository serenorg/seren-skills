"""Critical-only persistence tests for prophet-bounty-runner.

Reduced from plan §17.4 (6 tests) to 4 load-bearing assertions:
  1. exactly one `runs` row per invocation — guards against double-
     writes (would skew the operator's per-user run count) and
     skipped-writes (would orphan markets to a missing run).
  2. exactly one `markets_created` row per Prophet market — required
     for the operator's reconciler idempotency, which keys on
     prophet_market_id (plan §18.6).
  3. blocked-OTP runs persist with the §17.2 enum value `blocked_otp`,
     not a free-form string. The schema's runs.status CHECK refuses
     anything else; this test pins the runtime spelling so a typo
     doesn't surface only in production.
  4. status command's new local_markets_created surface (Phase 11)
     reflects the bounty-scoped count from storage.

The other 2 tests in plan §17.4 (status command does NOT call
prophet/polymarket, status command does NOT acquire OTP) are already
covered transitively by tests/test_smoke.py::
test_status_command_does_not_call_polymarket_or_prophet.
"""

from __future__ import annotations

from agent import run_command  # noqa: E402

from conftest import load_fixture  # type: ignore[import-not-found]


def _seed_happy_path(stub_gateway, stub_transport=None) -> None:
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
    # Issue #493: Prophet createMarket is now on the transport seam, not the gateway.
    if stub_transport is not None:
        stub_transport.register_default(load_fixture("prophet_create_market.json"))
    stub_gateway.register(
        "seren-bounty",
        "POST",
        "/bounties/bounty_fixture_001/submission",
        {"submission_id": "sub_fixture_001", "status": "submitted"},
    )


def test_persist_run_writes_exactly_one_runs_row_per_invocation(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    monkeypatch.setattr(
        "agent.acquire_prophet_token_via_otp",
        lambda *_args, **_kw: load_fixture("prophet_otp_session.json"),
    )
    _seed_happy_path(stub_gateway, stub_transport=stub_transport)

    run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    assert len(stub_storage.runs) == 1


def test_persist_writes_one_markets_created_row_per_executed_create(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    monkeypatch.setattr(
        "agent.acquire_prophet_token_via_otp",
        lambda *_args, **_kw: load_fixture("prophet_otp_session.json"),
    )
    _seed_happy_path(stub_gateway, stub_transport=stub_transport)

    result = run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    assert len(stub_storage.markets_created) == len(stub_transport.calls)
    assert len(stub_storage.markets_created) == len(
        result["prophet_markets_created"]
    )


def test_blocked_otp_run_persists_with_canonical_enum_value(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    def _raise_otp(*_args, **_kw):
        raise RuntimeError("otp_email_not_received_within_90s")

    monkeypatch.setattr("agent.acquire_prophet_token_via_otp", _raise_otp)
    stub_gateway.register(
        "seren-bounty",
        "POST",
        "/bounties/bounty_fixture_001/join",
        load_fixture("bounty_join.json"),
    )

    run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    statuses = [r.get("status") for r in stub_storage.runs]
    assert statuses == ["blocked_otp"]


def test_status_command_surfaces_local_markets_created_count(
    stub_gateway, stub_storage
) -> None:
    stub_gateway.register(
        "seren-bounty",
        "GET",
        "/users/me/earnings",
        load_fixture("earnings_zero.json"),
    )
    stub_storage.markets_created.extend(
        [
            {"prophet_market_id": "m1", "bounty_id": "bounty_fixture_001"},
            {"prophet_market_id": "m2", "bounty_id": "bounty_fixture_001"},
            {"prophet_market_id": "m3", "bounty_id": "other_bounty"},
        ]
    )
    request = {
        "command": "status",
        "bounty_id": "bounty_fixture_001",
        "json_output": True,
    }

    result = run_command(request, gateway=stub_gateway, storage=stub_storage)

    assert result["local_markets_created"] == 2
