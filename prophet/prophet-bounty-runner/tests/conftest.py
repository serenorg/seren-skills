from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def seed_prophet_chain_happy_path(
    stub_transport,
    *,
    market_id: str = "prophet_market_fixture_001",
    slug: str = "btc-100k-may-10-2026",
    resolution_date_iso: str = "2026-05-10T23:59:00Z",
    creator_viewer_id: str = "viewer_fixture_001",
) -> None:
    """Register a self-consistent four-step chain + post-create re-fetch.

    Phase-14a (#505) wires `MinimalProphetClient.create_market_chain`
    into the submission loop, so every smoke / persistence / boundary
    test now needs five chain operations plus the `MarketById`
    post-create query. Tests can override `resolution_date_iso` or
    `creator_viewer_id` to exercise the eligibility gates.

    Phase-14b (#505) wires the `MarketsForDedup` pre-filter before the
    chain. The default response here is the empty-connection shape so
    no candidate is dropped — tests exercising the duplicate path
    override `MarketsForDedup` with a populated `edges` list.
    """
    stub_transport.register(
        "MarketsForDedup",
        {"data": {"markets": {"edges": []}}},
    )
    # Issue #524 funds preflight: a comfortably-funded balance keeps
    # legacy happy-path tests focused on what they were written for
    # without doubling as funds-preflight tests.
    stub_transport.register(
        "ViewerWalletBalance",
        {"data": {"viewer": {"cashBalance": {"availableCents": 10000, "totalCents": 10000}}}},
    )
    stub_transport.register(
        "InitiateMarket",
        {"data": {"initiateMarket": {"draftId": f"draft_{market_id}"}}},
    )
    stub_transport.register(
        "StartOddsCalculation",
        {"data": {"startOddsCalculation": {"sessionId": f"session_{market_id}"}}},
    )
    stub_transport.register(
        "OddsCalculationSession",
        {
            "data": {
                "oddsCalculationSession": {
                    "status": "COMPLETED",
                    "odds": {"yes": 0.5, "no": 0.5},
                }
            }
        },
    )
    stub_transport.register(
        "MarketCreationOrderParams",
        {
            "data": {
                "marketCreationOrderParams": {
                    "params": {"orderType": "INITIAL", "betUsdc": 1}
                }
            }
        },
    )
    stub_transport.register(
        "CreateMarketWithBet",
        {"data": {"createMarketWithBet": {"market": {"id": market_id}}}},
    )
    stub_transport.register(
        "MarketById",
        {
            "data": {
                "market": {
                    "id": market_id,
                    "slug": slug,
                    "url": f"https://app.prophetmarket.ai/market/{slug}",
                    "resolutionDate": resolution_date_iso,
                    "creator": {"id": creator_viewer_id},
                }
            }
        },
    )


class StubGateway:
    """In-memory stand-in for the Seren publisher gateway.

    Tests register canned responses keyed by (publisher, method, path) and assert
    on the recorded call list. Used to verify fail-closed behavior — e.g. that
    Prophet createMarket is never called when the run is dry or OTP failed.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._responses: dict[tuple[str, str, str], Any] = {}
        self._failures: dict[tuple[str, str, str], Exception] = {}

    def register(self, publisher: str, method: str, path: str, response: Any) -> None:
        self._responses[(publisher, method.upper(), path)] = response

    def register_failure(self, publisher: str, method: str, path: str, exc: Exception) -> None:
        self._failures[(publisher, method.upper(), path)] = exc

    def call(
        self,
        publisher: str,
        method: str,
        path: str,
        body: dict | None = None,
        headers: dict | None = None,
    ) -> Any:
        key = (publisher, method.upper(), path)
        self.calls.append(
            {"publisher": publisher, "method": method.upper(), "path": path, "body": body}
        )
        if key in self._failures:
            raise self._failures[key]
        if key in self._responses:
            return self._responses[key]
        raise AssertionError(f"StubGateway: unregistered call {publisher} {method} {path}")

    def calls_to(self, publisher: str, method: str | None = None, path: str | None = None) -> list[dict]:
        result = [c for c in self.calls if c["publisher"] == publisher]
        if method is not None:
            result = [c for c in result if c["method"] == method.upper()]
        if path is not None:
            result = [c for c in result if c["path"] == path]
        return result


class StubProphetTransport:
    """In-memory stand-in for `prophet.transport.ProphetDirectTransport`.

    Issue #493: tests previously asserted on
    `stub_gateway.calls_to("prophet-ai", ...)` to verify that Prophet
    was reached. Prophet calls no longer touch the gateway, so this
    fixture replaces that seam.

    Tests register canned responses keyed by GraphQL `operationName`.
    Falls back to substring-matching the query body, then to the
    registered default. Records every call so tests can assert that
    Prophet was reached the expected number of times.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._by_operation: dict[str, Any] = {}
        self._by_query_substring: list[tuple[str, Any]] = []
        self._default_response: Any = None

    def register(self, operation_name: str, response: Any) -> None:
        """Register a canned response for a GraphQL operationName."""
        self._by_operation[operation_name] = response

    def register_by_query_substring(self, needle: str, response: Any) -> None:
        """Register a canned response matched when the query body contains `needle`."""
        self._by_query_substring.append((needle, response))

    def register_default(self, response: Any) -> None:
        self._default_response = response

    def post_graphql(
        self,
        *,
        jwt: str | None,
        query: str,
        variables: dict | None = None,
        operation_name: str | None = None,
    ) -> Any:
        self.calls.append(
            {
                "jwt": jwt,
                "query": query,
                "variables": variables,
                "operation_name": operation_name,
            }
        )
        chosen: Any = None
        if operation_name and operation_name in self._by_operation:
            chosen = self._by_operation[operation_name]
        else:
            for needle, resp in self._by_query_substring:
                if needle in query:
                    chosen = resp
                    break
            if chosen is None:
                # Default to operation_name-prefixed match by query content
                # (queries usually start with `query <OperationName>` or
                # `mutation <OperationName>`).
                for op, resp in self._by_operation.items():
                    if op in query:
                        chosen = resp
                        break
        if chosen is None:
            chosen = self._default_response
        if chosen is None:
            raise AssertionError(
                f"StubProphetTransport: no canned response for operation_name={operation_name!r}"
            )
        if isinstance(chosen, BaseException):
            raise chosen
        return chosen


class StubStorage:
    """In-memory stand-in for SerenDB persistence.

    Tests assert on the contents of `runs`, `submissions`, and `events` after
    a run. Mirrors the schema implied by spec.state.
    """

    def __init__(self) -> None:
        self.runs: list[dict] = []
        self.submissions: list[dict] = []
        self.events: list[dict] = []
        self.markets_created: list[dict] = []
        self.participant_identity: list[dict] = []

    def insert(self, table: str, row: dict) -> None:
        if not hasattr(self, table):
            raise AssertionError(f"StubStorage: unknown table {table!r}")
        getattr(self, table).append(row)


@pytest.fixture
def frozen_clock() -> datetime:
    return datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def stub_gateway() -> StubGateway:
    return StubGateway()


@pytest.fixture
def stub_storage() -> StubStorage:
    return StubStorage()


@pytest.fixture
def stub_transport() -> StubProphetTransport:
    return StubProphetTransport()


@pytest.fixture
def base_run_request() -> dict:
    return {
        "command": "run",
        "bounty_id": "bounty_fixture_001",
        "prophet_email": "implementer@example.com",
        "email_provider": "gmail",
        "candidate_limit": 12,
        "submit_limit": 3,
        "dry_run": False,
        "json_output": True,
    }
