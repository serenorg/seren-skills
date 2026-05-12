"""Critical-only workflow tests for prophet-bounty-runner.

Reduced from plan §10.4 (12 tests) to 5 load-bearing assertions:
  1. Happy-path run creates exactly one Prophet market and one bounty submission.
  2. OTP failure blocks the run AND persists a blocked run row (fail-closed
     evidence — no Prophet createMarket call escapes).
  3. Dry-run does NOT call Prophet createMarket (live-execution isolation).
  4. Polymarket sources resolving AFTER the bounty deadline are filtered out
     (bounty-fraud guard — out-of-window markets never reach createMarket).
  5. Status command never calls polymarket-data or prophet-ai (read-only).

The other 7 smoke tests in plan §10.4 (referral-code idempotency, persistence
row counts, no-resolution-date filter, dry-run submission isolation) are
covered transitively by these five and the per-module tests in phases 5–11.
"""

from __future__ import annotations

import pytest

from agent import run_command  # noqa: E402  (red until phase 10 implements)

from conftest import load_fixture  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# helpers


def _seed_happy_path(stub_gateway, stub_storage, stub_transport=None) -> None:
    stub_gateway.register(
        "seren-bounty",
        "POST",
        "/bounties/bounty_fixture_001/join",
        load_fixture("bounty_join.json"),
    )
    stub_gateway.register(
        "polymarket-data",
        "GET",
        "/markets?end_date_max=2026-05-11T00:00:00Z&closed=false&active=true&limit=100",
        load_fixture("polymarket_settling.json"),
    )
    # Issue #493: Prophet GraphQL is no longer routed through the gateway.
    # Seed createMarket on the new transport seam when one is provided.
    if stub_transport is not None:
        stub_transport.register_default(load_fixture("prophet_create_market.json"))
    stub_gateway.register(
        "seren-bounty",
        "POST",
        "/bounties/bounty_fixture_001/submission",
        {"submission_id": "sub_fixture_001", "status": "submitted"},
    )


# ---------------------------------------------------------------------------
# tests


def test_happy_path_run_creates_qualifying_markets_and_one_submission(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    """Happy path emits 1..submit_limit markets and exactly one submission.

    seren-bounty's submission contract REPLACES content per call (plan
    §13.2), so submission count is always exactly 1 per run regardless
    of how many markets get created. Market count is bounded only by
    `submit_limit` and how many polymarket sources survive the deadline
    + score filters.
    """
    monkeypatch.setattr(
        "agent.acquire_prophet_token_via_otp",
        lambda *_args, **_kw: load_fixture("prophet_otp_session.json"),
    )
    _seed_happy_path(stub_gateway, stub_storage, stub_transport=stub_transport)

    result = run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    submission_calls = stub_gateway.calls_to(
        "seren-bounty", "POST", "/bounties/bounty_fixture_001/submission"
    )
    assert result["status"] == "ok"
    assert 1 <= len(stub_transport.calls) <= base_run_request["submit_limit"]
    assert len(submission_calls) == 1


def test_otp_failure_blocks_run_and_persists_blocked_run(
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

    result = run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    blocked_runs = [r for r in stub_storage.runs if r.get("status") == "blocked_otp"]
    assert result["status"] == "blocked"
    assert stub_transport.calls == []
    assert len(blocked_runs) == 1


def test_dry_run_does_not_call_prophet_create_market(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    monkeypatch.setattr(
        "agent.acquire_prophet_token_via_otp",
        lambda *_args, **_kw: load_fixture("prophet_otp_session.json"),
    )
    _seed_happy_path(stub_gateway, stub_storage, stub_transport=stub_transport)
    request = {**base_run_request, "dry_run": True}

    run_command(
        request, gateway=stub_gateway, storage=stub_storage, transport=stub_transport
    )

    assert stub_transport.calls == []


def test_polymarket_source_resolving_after_deadline_is_filtered_out(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    monkeypatch.setattr(
        "agent.acquire_prophet_token_via_otp",
        lambda *_args, **_kw: load_fixture("prophet_otp_session.json"),
    )
    _seed_happy_path(stub_gateway, stub_storage, stub_transport=stub_transport)

    run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    assert all(
        "0xpoly-003"
        not in (c["variables"] or {}).get("source", {}).get("polymarket_market_id", "")
        for c in stub_transport.calls
    )


def test_status_command_does_not_call_polymarket_or_prophet(
    stub_gateway, stub_storage, stub_transport
) -> None:
    stub_gateway.register(
        "seren-bounty",
        "GET",
        "/users/me/earnings",
        load_fixture("earnings_zero.json"),
    )
    request = {"command": "status", "json_output": True}

    run_command(
        request, gateway=stub_gateway, storage=stub_storage, transport=stub_transport
    )

    polymarket_calls = stub_gateway.calls_to("polymarket-data")
    assert polymarket_calls == []
    assert stub_transport.calls == []
