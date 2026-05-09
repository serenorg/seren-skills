"""Phase 6: Prophet GraphQL minimal client.

Public surface (only these are imported by callers):

  - MinimalProphetClient — wraps gateway.call('prophet-ai', ...)
  - exceptions: ProphetUnauthorized, ProphetGraphQLError, ProphetSchemaError

Plan §12.1, §12.2. The client never touches httpx or the Prophet web URL
directly — it always goes through the gateway and the prophet-ai
publisher. SEREN_API_KEY rides on the gateway-side auth; the Privy JWT
is passed verbatim via the Authorization passthrough header.
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
