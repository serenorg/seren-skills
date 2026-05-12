"""Plan §22 Acceptance #15 — post-create resolution-date gate.

The bounty pays only for Prophet markets resolving STRICTLY before
`2026-05-26T00:00:00Z`. The polymarket-discovery filter blocks
out-of-window candidates pre-submit (covered in test_smoke.py), but the
operator can still slip a candidate whose Prophet market lands at or
after the deadline (Prophet rounds the resolutionDate, the source
mutates between fetch and submit, etc). The post-create gate at
`agent.py::create_markets` is the load-bearing guard.

Two boundary cases pinned here:
- Pass: prophet returns `resolutionDate = 2026-05-25T23:59:59Z`
  (one second before the deadline). Market lands in `markets_created`,
  no `prophet.market_resolution_date_ineligible` event emitted.
- Fail: prophet returns `resolutionDate = 2026-05-26T00:00:00Z` exactly
  (boundary inclusive of the deadline -> ineligible). Market does NOT
  land in `markets_created`; one
  `prophet.market_resolution_date_ineligible` event records the
  rejection.
"""

from __future__ import annotations

from copy import deepcopy

from agent import run_command
from conftest import load_fixture


def _seed(stub_gateway, stub_transport, prophet_create_response) -> None:
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
    # Issue #493: Prophet createMarket lives on the transport seam now.
    stub_transport.register_default(prophet_create_response)
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


def test_resolution_date_one_second_before_deadline_is_eligible(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    response = deepcopy(load_fixture("prophet_create_market.json"))
    response["data"]["createMarket"]["resolutionDate"] = "2026-05-25T23:59:59Z"
    _otp(monkeypatch)
    _seed(stub_gateway, stub_transport, response)

    result = run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    ineligible = [
        e for e in stub_storage.events
        if e.get("event_type") == "prophet.market_resolution_date_ineligible"
    ]
    assert result["status"] == "ok"
    assert ineligible == []
    assert any(
        m.get("resolves_at") == "2026-05-25T23:59:59Z"
        for m in stub_storage.markets_created
    )


def test_resolution_date_at_deadline_is_rejected(
    base_run_request, stub_gateway, stub_storage, stub_transport, monkeypatch
) -> None:
    response = deepcopy(load_fixture("prophet_create_market.json"))
    response["data"]["createMarket"]["resolutionDate"] = "2026-05-26T00:00:00Z"
    _otp(monkeypatch)
    _seed(stub_gateway, stub_transport, response)

    run_command(
        base_run_request,
        gateway=stub_gateway,
        storage=stub_storage,
        transport=stub_transport,
    )

    ineligible = [
        e for e in stub_storage.events
        if e.get("event_type") == "prophet.market_resolution_date_ineligible"
    ]
    rejected_at_deadline = [
        m for m in stub_storage.markets_created
        if m.get("resolves_at") == "2026-05-26T00:00:00Z"
    ]
    assert len(ineligible) >= 1
    assert rejected_at_deadline == []
