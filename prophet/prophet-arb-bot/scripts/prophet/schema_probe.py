"""One-shot live introspection of the prophet-ai GraphQL schema.

Plan §12.3: do not skip. Run this once during Phase 14 acceptance with
a fresh Privy JWT and write the result to
`tests/fixtures/prophet_schema.json`. The client and tests assert
against the captured fixture, not against guesses in the plan document.

Usage:

    export SEREN_API_KEY=...
    export PROPHET_SESSION_TOKEN='eyJ...'   # fresh Privy JWT
    python3 scripts/prophet/schema_probe.py \\
        --output tests/fixtures/prophet_schema.json

The probe only needs `Query.viewer`, `Query.market`, `Query.markets`,
and the four create-chain mutation types — full schema introspection
returns more than we need but is harmless to capture.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

INTROSPECTION_QUERY = """
query IntrospectProphet {
  __schema {
    queryType { name }
    mutationType { name }
    types {
      name
      kind
      fields {
        name
        type {
          name
          kind
          ofType { name kind ofType { name kind } }
        }
        args {
          name
          type {
            name
            kind
            ofType { name kind ofType { name kind } }
          }
        }
      }
      inputFields {
        name
        type {
          name
          kind
          ofType { name kind ofType { name kind } }
        }
      }
    }
  }
}
"""

GATEWAY_URL = "https://api.serendb.com/publishers/prophet-ai/api/graphql"


def _ssl_context() -> ssl.SSLContext:
    """Mirror db._ssl_context: prefer certifi, fall back to default trust.

    macOS Python (system + python.org) does not consult the keychain by
    default, so urlopen without an explicit context fails with
    CERTIFICATE_VERIFY_FAILED. The certifi bundle ships with most
    Python installs (we already require psycopg2-binary, which depends
    on it transitively).
    """
    try:
        import certifi  # type: ignore[import-not-found]

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def fetch_schema(*, seren_api_key: str, privy_jwt: str | None) -> dict:
    """Fire the introspection query through the Seren gateway.

    Auth (issue #485): the Seren gateway only accepts
    `Authorization: Bearer ...` and returns HTTP 401 to anything else
    (including `X-Seren-Api-Key`). When the user provides a Privy JWT,
    it takes precedence — matching ProphetOrderClient/HttpGateway
    behavior so auth-gated introspection fields surface.

    Response unwrap (issue #485): the gateway wraps publisher payloads
    as `{"data": {"status": 200, "body": <graphql>, "cost": ..., ...}}`.
    Tooling and the saved fixture expect the canonical GraphQL shape
    (`{"data": {"__schema": {...}}}`), so we strip the envelope here.
    """
    auth_token = privy_jwt or seren_api_key
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_token}",
    }

    body = json.dumps(
        {"query": INTROSPECTION_QUERY, "variables": {}}
    ).encode("utf-8")
    request = urllib.request.Request(
        GATEWAY_URL, data=body, method="POST", headers=headers
    )
    try:
        with urllib.request.urlopen(
            request, timeout=30, context=_ssl_context()
        ) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(
            f"prophet-ai introspection failed: HTTP {exc.code}\n{message}"
        ) from exc

    return _unwrap_gateway_envelope(payload)


def _unwrap_gateway_envelope(payload: dict) -> dict:
    """Return the inner GraphQL payload regardless of gateway wrapping.

    Wrapped form:   `{"data": {"status": 200, "body": <graphql>, ...}}`
    Unwrapped form: `{"data": {"__schema": ...}}` or `{"errors": [...]}`

    Detection key: gateway wrappers carry both `status` and `body` and
    do NOT have `__schema` directly under `data`. Unwrapped payloads
    have `__schema` directly under `data`. This keeps callers writing
    `payload["data"]["__schema"]` regardless of whether the gateway
    changes its wrapping behavior in the future.
    """
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data")
    if (
        isinstance(data, dict)
        and "body" in data
        and "status" in data
        and "__schema" not in data
    ):
        body = data.get("body")
        if isinstance(body, dict):
            return body
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--output",
        default="tests/fixtures/prophet_schema.json",
        help="Where to write the introspection result (default: tests/fixtures/prophet_schema.json).",
    )
    args = parser.parse_args(argv)

    seren_api_key = os.environ.get("SEREN_API_KEY")
    if not seren_api_key:
        print("error: SEREN_API_KEY environment variable is required", file=sys.stderr)
        return 1

    privy_jwt = os.environ.get("PROPHET_SESSION_TOKEN")
    if not privy_jwt:
        print(
            "warning: PROPHET_SESSION_TOKEN not set — introspecting public schema only",
            file=sys.stderr,
        )

    schema = fetch_schema(seren_api_key=seren_api_key, privy_jwt=privy_jwt)
    if "errors" in schema and schema["errors"]:
        print(
            f"error: introspection returned GraphQL errors:\n{json.dumps(schema['errors'], indent=2)}",
            file=sys.stderr,
        )
        return 2

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
    type_count = len(((schema.get("data") or {}).get("__schema") or {}).get("types") or [])
    print(f"wrote {out_path} ({type_count} types)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
