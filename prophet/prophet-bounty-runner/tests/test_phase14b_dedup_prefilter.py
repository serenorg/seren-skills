"""Phase 14b dedup pre-filter regression tests (#505 plan §14.3).

Three critical assertions:

  1. Agent calls `markets_for_dedup` BEFORE the create_market_chain
     submission loop. Without this ordering, the agent would happily
     duplicate an existing Prophet market, which the bounty operator
     would reject during reconciliation.

  2. Candidates whose `question` already exists on Prophet are
     dropped from the submission set. Exact-match on question is the
     plan §14.3 contract — title rewording can be handled by Prophet's
     own MarketValidationResult.isDuplicate path later.

  3. When `markets_for_dedup` raises `ProphetSchemaError` or
     `ProphetGraphQLError`, the run fails closed with
     `status=blocked, reason=prophet_dedup_unavailable` instead of
     submitting markets blind. Fail-open here would risk shipping
     dupes through reconciliation.

The captured schema (`tests/fixtures/prophet_schema.json`) confirms
`Query.markets(input: MarketsInput)` with `MarketFilter.resolvingBefore`
and `.status`. These tests pin those field names.
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


def _otp(monkeypatch) -> None:
    monkeypatch.setattr(
        "agent.acquire_prophet_token_via_otp",
        lambda *_args, **_kw: load_fixture("prophet_otp_session.json"),
    )


# ---------------------------------------------------------------------------
# Test 1: dedup query runs BEFORE chain submission, and the query uses
# `MarketsInput.filter` per the captured fixture.


def test_markets_for_dedup_fires_before_chain_submission_with_correct_shape(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    _otp(monkeypatch)
    _seed_polymarket_and_bounty(stub_gateway)
    seed_prophet_chain_happy_path(stub_transport)
    # Dedup returns "no existing markets" → chain proceeds.
    stub_transport.register(
        "MarketsForDedup",
        {"data": {"markets": {"edges": []}}},
    )

    run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    op_names = [c.get("operation_name") for c in stub_transport.calls]
    assert "MarketsForDedup" in op_names, (
        f"Dedup pre-filter not invoked: {op_names}"
    )
    # Ordering check: MarketsForDedup must appear before the first chain step.
    dedup_idx = op_names.index("MarketsForDedup")
    initiate_idx = (
        op_names.index("InitiateMarket") if "InitiateMarket" in op_names else 10**9
    )
    assert dedup_idx < initiate_idx, (
        f"Dedup must precede chain submission; order was {op_names}"
    )

    # Query shape check: the dedup call passes a `MarketsInput` with a
    # `filter` field, and the filter uses `resolvingBefore` (the captured
    # schema's `MarketFilter` field name — not the legacy `limit`/`status`
    # flat shape).
    dedup_calls = [
        c for c in stub_transport.calls if c.get("operation_name") == "MarketsForDedup"
    ]
    assert len(dedup_calls) == 1
    vars_ = dedup_calls[0].get("variables") or {}
    input_ = vars_.get("input") or {}
    assert "filter" in input_, (
        f"MarketsInput must have a `filter` key per captured fixture; got {input_!r}"
    )
    flt = input_["filter"]
    assert "resolvingBefore" in flt, (
        f"MarketFilter.resolvingBefore is missing; got {flt!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: candidates whose question already exists in Prophet are dropped.


def test_dedup_drops_candidates_with_exact_question_match(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    """Polymarket sources include `poly-001` ("Will BTC close above $100k on
    May 10 2026?"). Seed Prophet's dedup response to claim this question is
    already listed — the agent must drop it and only submit the remaining
    candidate(s)."""
    _otp(monkeypatch)
    _seed_polymarket_and_bounty(stub_gateway)
    seed_prophet_chain_happy_path(stub_transport)
    stub_transport.register(
        "MarketsForDedup",
        {
            "data": {
                "markets": {
                    "edges": [
                        {
                            "node": {
                                "id": "prophet_existing_market_001",
                                "slug": "btc-100k-may-10-2026",
                                "question": (
                                    "Will BTC close above $100k on May 10 2026?"
                                ),
                                "resolutionDate": "2026-05-10T23:59:00Z",
                            }
                        }
                    ]
                }
            }
        },
    )

    result = run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    # The poly-001 question must NOT appear in any InitiateMarket call.
    initiate_questions = [
        ((c.get("variables") or {}).get("input") or {}).get("question", "")
        for c in stub_transport.calls
        if c.get("operation_name") == "InitiateMarket"
    ]
    assert "Will BTC close above $100k on May 10 2026?" not in initiate_questions
    assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Test 3: dedup failure → fail-closed (no chain submission, blocked status).


def test_dedup_failure_blocks_run_and_skips_chain(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    """Plan §14.3 fail-closed contract: if Prophet's markets query is
    unreachable, the run must NOT submit candidates blind — it must
    return `status=blocked, reason=prophet_dedup_unavailable` and never
    enter the chain submission loop."""
    _otp(monkeypatch)
    _seed_polymarket_and_bounty(stub_gateway)
    seed_prophet_chain_happy_path(stub_transport)
    stub_transport.register(
        "MarketsForDedup",
        ProphetSchemaError("markets query returned 502 from Prophet"),
    )

    result = run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    op_names = [c.get("operation_name") for c in stub_transport.calls]
    assert "InitiateMarket" not in op_names, (
        f"Chain must NOT be reached when dedup fails: {op_names}"
    )
    assert result["status"] == "blocked"
    assert result["reason"] == "prophet_dedup_unavailable"
    assert isinstance(result.get("blockers"), list) and result["blockers"]
    assert any("markets query" in b or "502" in b for b in result["blockers"])
