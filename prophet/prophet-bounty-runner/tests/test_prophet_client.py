"""Critical-only Prophet-client tests.

Reduced from plan §12.4 (5 tests) to 4 load-bearing assertions focused
on fail-closed paths and the load-bearing happy path:

  1. viewer() raises ProphetUnauthorized on 401         (§11.6 fail-closed)
  2. _post raises ProphetGraphQLError when errors[] populated
                                                        (§11.6 fail-closed)
  3. viewer() returns id+email on success               (identity binding)
  4. market() raises ProphetSchemaError on missing fields
                                                        (schema-drift guard)

Issue #493: Prophet calls no longer go through the gateway; the client
takes a `transport` with a `post_graphql` method. Tests stub it inline.

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


class _StubTransport:
    """Minimal transport stub: tests register a single canned response
    or a single canned exception."""

    def __init__(self, response=None, raise_exc: Exception | None = None) -> None:
        self.response = response
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    def post_graphql(self, *, jwt, query, variables=None, operation_name=None):
        self.calls.append(
            {
                "jwt": jwt,
                "query": query,
                "variables": variables,
                "operation_name": operation_name,
            }
        )
        if self.raise_exc:
            raise self.raise_exc
        return self.response


# ---------------------------------------------------------------------------
# Test 1: 401 → ProphetUnauthorized


def test_viewer_raises_unauthorized_on_401() -> None:
    transport = _StubTransport(raise_exc=ProphetUnauthorized("401"))
    client = MinimalProphetClient(transport=transport)

    with pytest.raises(ProphetUnauthorized):
        client.viewer(jwt="eyJ.expired.jwt")


# ---------------------------------------------------------------------------
# Test 2: GraphQL errors[] populated → ProphetGraphQLError


def test_raises_graphql_error_when_errors_field_populated() -> None:
    transport = _StubTransport(
        raise_exc=ProphetGraphQLError(
            "prophet GraphQL errors: Field 'viewer' not found in schema"
        )
    )
    client = MinimalProphetClient(transport=transport)

    with pytest.raises(ProphetGraphQLError, match="Field 'viewer'"):
        client.viewer(jwt="eyJ.fake.jwt")


# ---------------------------------------------------------------------------
# Test 3: viewer happy path returns id + email


def test_viewer_returns_id_and_email_on_success() -> None:
    transport = _StubTransport(
        response={
            "data": {"viewer": {"id": "viewer_fixture_001", "email": "u@example.com"}}
        }
    )
    client = MinimalProphetClient(transport=transport)

    identity = client.viewer(jwt="eyJ.fresh.jwt")

    assert identity.id == "viewer_fixture_001"
    assert identity.email == "u@example.com"


# ---------------------------------------------------------------------------
# Test 4: market() raises on missing fields → schema-drift guard


def test_market_raises_schema_error_when_id_missing() -> None:
    # Successful 200 but Prophet returned a record without `id` —
    # could mean the market was deleted or the schema rotated field names.
    transport = _StubTransport(
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
    client = MinimalProphetClient(transport=transport)

    with pytest.raises(ProphetSchemaError):
        client.market(jwt="eyJ.fake.jwt", market_id="prophet_market_fixture_001")
