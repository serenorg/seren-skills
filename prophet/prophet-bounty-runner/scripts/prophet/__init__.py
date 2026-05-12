"""Phase 6: Prophet GraphQL minimal client.

Public surface (only these are imported by callers):

  - MinimalProphetClient — composes operations over a ProphetTransport.
  - ProphetDirectTransport (transport.py) — direct HTTP to Prophet.
  - exceptions: ProphetUnauthorized, ProphetGraphQLError, ProphetSchemaError

Issue #493: every authenticated Prophet call now goes directly to
`https://app.prophetmarket.ai/api/graphql` with `Authorization: Bearer
<Privy JWT>`. The previous `prophet-ai` Seren publisher hop was
removed because the gateway reserves `Authorization` for SEREN_API_KEY
billing auth — there is no way to ride a Privy JWT through that slot
without colliding, and Prophet ignored the `Cookie: privy-token=*`
workaround entirely.
"""

from __future__ import annotations


class ProphetClientError(Exception):
    """Base for all Prophet-client exceptions."""


class ProphetUnauthorized(ProphetClientError):
    """Prophet returned 401 — JWT expired or invalid.

    Per plan §11.6 the caller (AuthFacade) should react by flipping the
    cache to needs_otp; bubbling up here lets the run record the failure.
    """


class ProphetGraphQLError(ProphetClientError):
    """Prophet returned 200 with a populated `errors` field.

    GraphQL servers return 200 even on logical errors; if the response
    has an `errors` array, that's a hard failure. Surface it instead of
    silently treating partial data as success.
    """


class ProphetSchemaError(ProphetClientError):
    """Response shape did not match what the client expected.

    Most likely means the live schema has drifted from the captured
    fixture and `schema_probe.py` needs to re-run.
    """


__all__ = [
    "ProphetClientError",
    "ProphetUnauthorized",
    "ProphetGraphQLError",
    "ProphetSchemaError",
]
