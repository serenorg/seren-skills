"""Critical regression test for #493 — prophet-arb-bot direct path.

Mirrors the bounty-runner test. Asserts that `ProphetDirectTransport.post_graphql`
issues the exact HTTP shape Prophet's API requires:

  POST https://app.prophetmarket.ai/api/graphql
  Authorization: Bearer <Privy JWT>

and does NOT emit Cookie or a colliding SEREN_API_KEY. See issue #493
for the full live-evidence audit.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from prophet import ProphetGraphQLError, ProphetUnauthorized
from prophet.transport import ProphetDirectTransport


class _FakeHTTPResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._payload


def test_post_graphql_uses_authorization_bearer_against_prophet():
    transport = ProphetDirectTransport()
    captured: dict = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeHTTPResponse(b'{"data":{"placeOrder":{"orderId":"o1"}}}')

    with patch("prophet.transport.urlopen", new=fake_urlopen):
        result = transport.post_graphql(
            jwt="eyJ.privy.jwt",
            query="mutation PlaceOrder($input: PlaceOrderInput!) { placeOrder(input: $input) { orderId } }",
            variables={"input": {}},
            operation_name="PlaceOrder",
        )

    assert captured["url"] == "https://app.prophetmarket.ai/api/graphql"
    assert captured["method"] == "POST"
    assert captured["headers"].get("authorization") == "Bearer eyJ.privy.jwt"
    assert "cookie" not in captured["headers"], (
        "regression guard: dropping the dead Cookie: privy-token=* path"
    )
    assert result == {"data": {"placeOrder": {"orderId": "o1"}}}


def test_post_graphql_maps_401_to_prophet_unauthorized():
    from urllib.error import HTTPError

    transport = ProphetDirectTransport()

    def fake_urlopen(req, timeout=None, context=None):
        raise HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=None)

    with patch("prophet.transport.urlopen", new=fake_urlopen):
        with pytest.raises(ProphetUnauthorized):
            transport.post_graphql(jwt="stale", query="query { viewer { user { id } } }")


def test_post_graphql_raises_on_graphql_errors_payload():
    transport = ProphetDirectTransport()

    def fake_urlopen(req, timeout=None, context=None):
        return _FakeHTTPResponse(
            b'{"errors":[{"message":"validation failed"}],"data":null}'
        )

    with patch("prophet.transport.urlopen", new=fake_urlopen):
        with pytest.raises(ProphetGraphQLError):
            transport.post_graphql(jwt="ok", query="query { viewer { user { id } } }")


def test_post_graphql_honors_prophet_base_url_override():
    transport = ProphetDirectTransport(base_url="https://testnet.prophetmarket.ai")
    captured: dict = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        return _FakeHTTPResponse(b'{"data":{}}')

    with patch("prophet.transport.urlopen", new=fake_urlopen):
        transport.post_graphql(jwt="tn", query="query { __typename }")

    assert captured["url"] == "https://testnet.prophetmarket.ai/api/graphql"
