from __future__ import annotations

import json
import sys
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


class StubGateway:
    """In-memory stand-in for the Seren publisher gateway.

    Tests register canned responses keyed by (publisher, method, path)
    and assert on the recorded call list. Mirrors the seam used by every
    Seren skill — same shape as the bounty-runner's StubGateway so test
    helpers can be cross-pollinated later.
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
            {
                "publisher": publisher,
                "method": method.upper(),
                "path": path,
                "body": body,
                "headers": headers or {},
            }
        )
        if key in self._failures:
            raise self._failures[key]
        if key in self._responses:
            return self._responses[key]
        raise AssertionError(f"StubGateway: unregistered call {publisher} {method} {path}")

    def calls_to(
        self,
        publisher: str,
        method: str | None = None,
        path: str | None = None,
    ) -> list[dict]:
        result = [c for c in self.calls if c["publisher"] == publisher]
        if method is not None:
            result = [c for c in result if c["method"] == method.upper()]
        if path is not None:
            result = [c for c in result if c["path"] == path]
        return result


class StubProphetTransport:
    """In-memory stand-in for `prophet.transport.ProphetDirectTransport`.

    Issue #493: Prophet calls no longer go through the publisher gateway.
    Tests register canned responses by GraphQL `operationName` (preferred)
    or by a substring match on the query body, then assert on `calls`.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._by_operation: dict[str, Any] = {}
        self._by_query_substring: list[tuple[str, Any]] = []
        self._default_response: Any = None

    def register(self, operation_name: str, response: Any) -> None:
        self._by_operation[operation_name] = response

    def register_by_query_substring(self, needle: str, response: Any) -> None:
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


@pytest.fixture
def stub_gateway() -> StubGateway:
    return StubGateway()


@pytest.fixture
def stub_transport() -> StubProphetTransport:
    return StubProphetTransport()
