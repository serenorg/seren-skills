"""Critical-only Prophet-client tests.

Reduced from plan §12.4 (5 tests) to 4 load-bearing assertions focused
on fail-closed paths and the load-bearing happy path:

  1. viewer() raises ProphetUnauthorized on 401         (§11.6 fail-closed)
  2. _post raises ProphetGraphQLError when errors[] populated
                                                        (§11.6 fail-closed)
  3. viewer() returns id+email on success               (identity binding)
  4. market() raises ProphetSchemaError on missing fields
                                                        (schema-drift guard)

Skipped per critical-only doctrine:
  - test_prophet_client_serializes_create_market_input  — input shape
    depends on schema_probe.py output, validated during Phase 14
    acceptance against the captured fixture, not now
  - test_prophet_client_returns_market_id_and_url_on_success — happy path
    is exercised transitively by Phase 10's smoke tests via run_command
"""

from __future__ import annotations

import pytest

from prophet import (  # noqa: E402
    ProphetGraphQLError,
    ProphetSchemaError,
    ProphetUnauthorized,
)
from prophet.client import MinimalProphetClient  # noqa: E402


class _StubGateway:
    """Minimal gateway stub. Tests register a single canned response."""

    def __init__(self, response=None, raise_status: int | None = None) -> None:
        self.response = response
        self.raise_status = raise_status
        self.calls: list[dict] = []

    def call(self, publisher, method, path, body=None, headers=None):
        self.calls.append(
            {"publisher": publisher, "method": method, "path": path, "body": body, "headers": headers}
        )
        if self.raise_status == 401:
            return {"status": 401, "error": "unauthorized"}
        return self.response


# ---------------------------------------------------------------------------
# Test 1: 401 → ProphetUnauthorized


def test_viewer_raises_unauthorized_on_401() -> None:
    gateway = _StubGateway(raise_status=401)
    client = MinimalProphetClient(gateway=gateway)

    with pytest.raises(ProphetUnauthorized):
        client.viewer(jwt="eyJ.expired.jwt")


# ---------------------------------------------------------------------------
# Test 2: GraphQL errors[] populated → ProphetGraphQLError


def test_raises_graphql_error_when_errors_field_populated() -> None:
    gateway = _StubGateway(
        response={
            "data": None,
            "errors": [{"message": "Field 'viewer' not found in schema"}],
        }
    )
    client = MinimalProphetClient(gateway=gateway)

    with pytest.raises(ProphetGraphQLError, match="Field 'viewer'"):
        client.viewer(jwt="eyJ.fake.jwt")


# ---------------------------------------------------------------------------
# Test 3: viewer happy path returns id + email


def test_viewer_returns_id_and_email_on_success() -> None:
    gateway = _StubGateway(
        response={
            "data": {"viewer": {"id": "viewer_fixture_001", "email": "u@example.com"}}
        }
    )
    client = MinimalProphetClient(gateway=gateway)

    identity = client.viewer(jwt="eyJ.fresh.jwt")

    assert identity.id == "viewer_fixture_001"
    assert identity.email == "u@example.com"


# ---------------------------------------------------------------------------
# Test 4: market() raises on missing fields → schema-drift guard


def test_market_raises_schema_error_when_id_missing() -> None:
    # Successful 200 but Prophet returned a record without `id` —
    # could mean the market was deleted or the schema rotated field names.
    gateway = _StubGateway(
        response={
            "data": {
                "market": {
                    "slug": "btc-100k-may-10-2026",
                    "resolutionDate": "2026-05-10T23:59:00Z",
                    "creator": {"id": "viewer_fixture_001"},
                }
            }
        }
    )
    client = MinimalProphetClient(gateway=gateway)

    with pytest.raises(ProphetSchemaError):
        client.market(jwt="eyJ.fake.jwt", market_id="prophet_market_fixture_001")
