"""Critical regression test for #493 — prophet-bounty-runner direct path.

Asserts that `ProphetDirectTransport.post_graphql` issues the exact HTTP
shape Prophet's API requires:

  POST https://app.prophetmarket.ai/api/graphql
  Authorization: Bearer <Privy JWT>

and does NOT emit:

  - Cookie: privy-token=*  (Prophet ignores cookies for viewer-binding)
  - Authorization: Bearer <SEREN_API_KEY>  (this is what collided with
    the JWT under the old prophet-ai publisher hop)

Live evidence (2026-05-12) recorded on issue #493: only this shape
returns a non-null `viewer.user.id`. Any regression to the gateway hop
or the Cookie path silently reverts the run to status=blocked_otp, so
this test is the load-bearing guard.
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
        captured["body"] = req.data
        return _FakeHTTPResponse(b'{"data":{"viewer":{"user":{"id":"u1","email":"a@b"}}}}')

    with patch("prophet.transport.urlopen", new=fake_urlopen):
        result = transport.post_graphql(
            jwt="eyJ.privy.jwt",
            query="query Viewer { viewer { user { id email } } }",
            variables={},
            operation_name="Viewer",
        )

    assert captured["url"] == "https://app.prophetmarket.ai/api/graphql", (
        "must hit Prophet directly, NOT publishers/prophet-ai"
    )
    assert captured["method"] == "POST"
    assert captured["headers"].get("authorization") == "Bearer eyJ.privy.jwt", (
        "Privy JWT must ride on Authorization: Bearer — Prophet ignores cookies"
    )
    assert "cookie" not in captured["headers"], (
        "regression guard: dropping the dead Cookie: privy-token=* path"
    )
    assert "SEREN_API_KEY" not in (captured["headers"].get("authorization") or ""), (
        "regression guard: gateway SEREN_API_KEY must never share the Auth slot"
    )
    assert result == {"data": {"viewer": {"user": {"id": "u1", "email": "a@b"}}}}


def test_post_graphql_maps_401_to_prophet_unauthorized():
    """401 must propagate so AuthFacade can flip the cache to needs_otp.

    Required by §11.6 fail-closed contract — verified live, since the
    old gateway path returned 400 instead and masked the auth signal.
    """
    from urllib.error import HTTPError

    transport = ProphetDirectTransport()

    def fake_urlopen(req, timeout=None, context=None):
        raise HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=None)

    with patch("prophet.transport.urlopen", new=fake_urlopen):
        with pytest.raises(ProphetUnauthorized):
            transport.post_graphql(jwt="stale", query="query { viewer { user { id } } }")


def test_post_graphql_raises_on_graphql_errors_payload():
    """GraphQL servers return 200 with `errors[]` on logical failures.

    Required so dedup/post-create guards in agent.py and the resolver
    chain in client.py see them as ProphetGraphQLError instead of
    silently treating partial data as success (§11.6 fail-closed).
    """
    transport = ProphetDirectTransport()

    def fake_urlopen(req, timeout=None, context=None):
        return _FakeHTTPResponse(
            b'{"errors":[{"message":"validation failed"}],"data":null}'
        )

    with patch("prophet.transport.urlopen", new=fake_urlopen):
        with pytest.raises(ProphetGraphQLError):
            transport.post_graphql(jwt="ok", query="query { viewer { user { id } } }")


def test_post_graphql_honors_prophet_base_url_override():
    """Testnet routing must work without code changes.

    The 3 sibling Prophet skills already use this convention via
    PROPHET_BASE_URL. Keeping it consistent here unlocks the same
    testnet workflow for bounty-runner without per-call plumbing.
    """
    transport = ProphetDirectTransport(base_url="https://testnet.prophetmarket.ai")
    captured: dict = {}

    def fake_urlopen(req, timeout=None, context=None):
        captured["url"] = req.full_url
        return _FakeHTTPResponse(b'{"data":{}}')

    with patch("prophet.transport.urlopen", new=fake_urlopen):
        transport.post_graphql(jwt="tn", query="query { __typename }")

    assert captured["url"] == "https://testnet.prophetmarket.ai/api/graphql"
