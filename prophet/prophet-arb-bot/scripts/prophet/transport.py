"""Direct-to-Prophet HTTP transport.

Replaces the `prophet-ai` Seren publisher hop for every authenticated
Prophet GraphQL call. Live evidence on issue #493 (2026-05-12) showed
the publisher proxy is structurally incompatible with Prophet's auth:

  - Prophet's /api/graphql only honors `Authorization: Bearer <JWT>`.
  - The gateway claims `Authorization` for SEREN_API_KEY billing auth,
    so the Privy JWT cannot ride that slot through the proxy.
  - The gateway whitelists `Cookie` for 1:1 passthrough, but Prophet
    ignores cookies for viewer-binding.

The fix is to talk to Prophet directly. This module is the single seam
through which every authenticated Prophet call now flows; tests stub
it via the StubProphetTransport fixture in conftest.

`PROPHET_BASE_URL` env var routes to testnet without code changes —
mirrors the convention already used by prophet-adversarial-auditor,
prophet-growth-agent, and prophet-market-seeder.
"""

from __future__ import annotations

import json
import os
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import ProphetGraphQLError, ProphetUnauthorized

DEFAULT_BASE_URL = "https://app.prophetmarket.ai"
GRAPHQL_PATH = "/api/graphql"
DEFAULT_TIMEOUT_SECONDS = 30.0


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


class ProphetDirectTransport:
    """HTTP transport for Prophet's GraphQL endpoint.

    Constructor params:
      base_url:        defaults to `PROPHET_BASE_URL` env var, then
                       `https://app.prophetmarket.ai`. Override for testnet.
      timeout_seconds: per-request timeout.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv("PROPHET_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self.timeout = timeout_seconds

    def post_graphql(
        self,
        *,
        jwt: str | None,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> dict[str, Any]:
        """POST a GraphQL operation to Prophet.

        Raises:
          ProphetUnauthorized  on HTTP 401.
          ProphetGraphQLError  on any other non-2xx, on transport error,
                               or on a 2xx response with a populated
                               `errors[]` array.
        """
        url = f"{self.base_url}{GRAPHQL_PATH}"
        body: dict[str, Any] = {"query": query}
        if variables is not None:
            body["variables"] = variables
        if operation_name:
            body["operationName"] = operation_name
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        req = Request(url, data=data, method="POST")
        req.add_header("Accept", "application/json")
        req.add_header("Content-Type", "application/json")
        if jwt:
            req.add_header("Authorization", f"Bearer {jwt}")

        try:
            with urlopen(req, timeout=self.timeout, context=_ssl_context()) as resp:
                text = resp.read().decode("utf-8")
        except HTTPError as exc:
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8")
            except Exception:
                err_body = ""
            if exc.code == 401:
                raise ProphetUnauthorized(
                    f"prophet returned 401: {err_body[:200]}"
                ) from exc
            raise ProphetGraphQLError(
                f"prophet HTTP {exc.code}: {err_body[:200]}"
            ) from exc
        except URLError as exc:
            raise ProphetGraphQLError(f"prophet transport error: {exc}") from exc

        if not text:
            return {}
        try:
            response = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProphetGraphQLError(
                f"prophet returned non-JSON body: {text[:200]}"
            ) from exc

        if not isinstance(response, dict):
            raise ProphetGraphQLError(
                f"prophet returned non-dict payload: {type(response).__name__}"
            )

        errors = response.get("errors")
        if errors:
            first = errors[0] if isinstance(errors, list) and errors else {}
            message = (
                first.get("message") if isinstance(first, dict) else str(first)
            ) or "unknown GraphQL error"
            raise ProphetGraphQLError(f"prophet GraphQL errors: {message}")

        return response
