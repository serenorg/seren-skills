"""One-shot live introspection of the Prophet GraphQL schema.

Plan §12.3: do not skip. Run this once during Phase 14 acceptance with
a fresh Privy JWT and write the result to
`tests/fixtures/prophet_schema.json`. The client and tests assert
against the captured fixture, not against guesses in the plan document.

Issue #493: this probe now talks directly to `app.prophetmarket.ai`
via `ProphetDirectTransport` — same path used by the production client.
The earlier `publishers/prophet-ai/api/graphql` URL is gone for the
reasons documented on #493 (publisher proxy reserves the
`Authorization` slot for SEREN_API_KEY billing auth, so the Privy JWT
cannot ride that slot through the proxy).

Usage:

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
from pathlib import Path

# Same scripts/ dir houses prophet/ — keep imports relative.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prophet.transport import ProphetDirectTransport  # noqa: E402

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


def fetch_schema(*, privy_jwt: str | None) -> dict:
    """Fire the introspection query directly at Prophet.

    Introspection is public so the JWT is optional, but we pass it when
    available so auth-gated fields surface.
    """
    transport = ProphetDirectTransport()
    try:
        return transport.post_graphql(
            jwt=privy_jwt,
            query=INTROSPECTION_QUERY,
            variables={},
            operation_name="IntrospectProphet",
        )
    except Exception as exc:
        raise SystemExit(f"prophet introspection failed: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument(
        "--output",
        default="tests/fixtures/prophet_schema.json",
        help="Where to write the introspection result (default: tests/fixtures/prophet_schema.json).",
    )
    args = parser.parse_args(argv)

    privy_jwt = os.environ.get("PROPHET_SESSION_TOKEN")
    if not privy_jwt:
        print(
            "warning: PROPHET_SESSION_TOKEN not set — introspecting public schema only",
            file=sys.stderr,
        )

    schema = fetch_schema(privy_jwt=privy_jwt)
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
