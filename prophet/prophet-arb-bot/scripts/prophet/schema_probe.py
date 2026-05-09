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
          ofType { name kind }
        }
      }
      inputFields {
        name
        type {
          name
          kind
          ofType { name kind }
        }
      }
    }
  }
}
"""

GATEWAY_URL = "https://api.serendb.com/publishers/prophet-ai/api/graphql"


def fetch_schema(*, seren_api_key: str, privy_jwt: str | None) -> dict:
    """Fire the introspection query through the gateway.

    The gateway forwards SEREN_API_KEY for billing/auth and the
    Authorization Bearer header through to Prophet for the JWT.
    Introspection is a public read so the JWT is optional, but we pass
    it when available so any auth-gated fields show up too.
    """
    headers = {
        "Content-Type": "application/json",
        "X-Seren-Api-Key": seren_api_key,
    }
    if privy_jwt:
        headers["Authorization"] = f"Bearer {privy_jwt}"

    body = json.dumps(
        {"query": INTROSPECTION_QUERY, "variables": {}}
    ).encode("utf-8")
    request = urllib.request.Request(
        GATEWAY_URL, data=body, method="POST", headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(
            f"prophet-ai introspection failed: HTTP {exc.code}\n{message}"
        ) from exc


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
