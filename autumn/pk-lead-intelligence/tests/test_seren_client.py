"""Unit tests for scripts/seren_client.py.

The wrapper layers cleanly:

- env / api-key resolution (pure)
- URL construction (pure)
- header construction (pure)
- response decoding (pure)
- transport orchestration via an injected fetcher

The actual HTTP transport (urllib) is not unit-tested here — it
takes one call against the live gateway in Phase 1's dry-run
checkpoint to validate. These tests pin the contract everything
else depends on.
"""

from __future__ import annotations

import pytest

from scripts import seren_client as sc


# --------------------------------------------------------------------- #
# API key resolution                                                    #
# --------------------------------------------------------------------- #


def test_resolve_api_key_prefers_API_KEY_over_SEREN_API_KEY(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Seren Desktop injects `API_KEY`. Existing skills prefer it
    when both are set so a desktop session does not get accidentally
    routed at a stale standalone key in the user's shell.
    """

    monkeypatch.setenv("API_KEY", "desktop-injected")
    monkeypatch.setenv("SEREN_API_KEY", "standalone-shell")
    assert sc.resolve_api_key() == "desktop-injected"


def test_resolve_api_key_falls_back_to_SEREN_API_KEY(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("SEREN_API_KEY", "standalone-shell")
    assert sc.resolve_api_key() == "standalone-shell"


def test_resolve_api_key_raises_when_neither_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SEREN_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="API_KEY"):
        sc.resolve_api_key()


def test_resolve_api_key_error_hints_at_seren_mcp_install_and_auth_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue #789 — Claude Cowork users hit this code path on every
    cold start. The error must hand them both recovery paths in one
    message so the discovery loop is one line, not a docs trawl:

      1. The `claude mcp add` install command for the hosted
         seren-mcp (the path SerenDesktop-equivalent users take).
      2. The `https://api.serendb.com/auth/agent` curl fallback
         (the path for users who cannot install MCP).

    A future refactor that silently drops either hint is a P0
    regression for the Cowork onboarding flow.
    """

    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SEREN_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        sc.resolve_api_key()

    message = str(excinfo.value)
    assert "claude mcp add" in message
    assert "https://mcp.serendb.com/mcp" in message
    assert "https://api.serendb.com/auth/agent" in message


# --------------------------------------------------------------------- #
# URL construction                                                      #
# --------------------------------------------------------------------- #


def test_build_url_basic_shape() -> None:
    assert (
        sc._build_url("seren-db", "/projects")
        == "https://api.serendb.com/publishers/seren-db/projects"
    )


def test_build_url_handles_path_without_leading_slash() -> None:
    assert (
        sc._build_url("seren-db", "projects")
        == "https://api.serendb.com/publishers/seren-db/projects"
    )


def test_build_url_strips_trailing_slash_from_publisher_slug() -> None:
    assert (
        sc._build_url("seren-db/", "/projects")
        == "https://api.serendb.com/publishers/seren-db/projects"
    )


def test_build_url_preserves_nested_paths() -> None:
    assert (
        sc._build_url("seren-db", "/projects/abc/connection_uri")
        == "https://api.serendb.com/publishers/seren-db/projects/abc/connection_uri"
    )


def test_build_url_rejects_empty_publisher() -> None:
    with pytest.raises(ValueError, match="publisher"):
        sc._build_url("", "/projects")


def test_build_url_rejects_empty_path() -> None:
    with pytest.raises(ValueError, match="path"):
        sc._build_url("seren-db", "")


def test_build_url_supports_custom_base_url() -> None:
    """Override exists for staging / local-gateway testing. Default is
    the public gateway. Custom base must not be mangled."""

    assert (
        sc._build_url(
            "seren-db",
            "/projects",
            base_url="http://localhost:8000",
        )
        == "http://localhost:8000/publishers/seren-db/projects"
    )


# --------------------------------------------------------------------- #
# Header construction                                                   #
# --------------------------------------------------------------------- #


def test_build_headers_attaches_bearer_token() -> None:
    headers = sc._build_headers("test-key")
    assert headers["Authorization"] == "Bearer test-key"


def test_build_headers_sets_json_content_type_when_body_provided() -> None:
    """JSON bodies need the matching Content-Type or the gateway
    refuses with a 415. Skip it on GET (no body) to keep request
    surface clean."""

    with_body = sc._build_headers("test-key", has_body=True)
    no_body = sc._build_headers("test-key", has_body=False)
    assert with_body["Content-Type"] == "application/json"
    assert "Content-Type" not in no_body


def test_build_headers_rejects_empty_api_key() -> None:
    with pytest.raises(ValueError, match="api_key"):
        sc._build_headers("")


# --------------------------------------------------------------------- #
# Response decoding                                                     #
# --------------------------------------------------------------------- #


def test_decode_response_returns_dict_on_2xx() -> None:
    assert sc._decode_response(200, b'{"ok": true}') == {"ok": True}
    assert sc._decode_response(201, b'{"id": "abc"}') == {"id": "abc"}


def test_decode_response_returns_empty_dict_on_204() -> None:
    """No-content responses (DELETE typically) are common and must
    not blow up parsing — they round-trip as an empty dict."""

    assert sc._decode_response(204, b"") == {}


def test_decode_response_raises_on_4xx_with_body_in_message() -> None:
    """A 4xx is almost always an actionable misconfiguration on our
    side. Surface the gateway's error body in the exception message
    so the operator can fix it without rerunning to read the log."""

    with pytest.raises(sc.PublisherError) as excinfo:
        sc._decode_response(400, b'{"error": "bad request"}')

    assert excinfo.value.status == 400
    assert "bad request" in str(excinfo.value)


def test_decode_response_raises_on_5xx() -> None:
    with pytest.raises(sc.PublisherError) as excinfo:
        sc._decode_response(503, b"service unavailable")

    assert excinfo.value.status == 503


def test_decode_response_raises_on_non_json_2xx_body() -> None:
    """If a publisher returns 2xx but garbage in the body, the wrapper
    must surface that as a structured failure — not let the JSON
    decode error bubble through with a confusing message."""

    with pytest.raises(sc.PublisherError, match="JSON"):
        sc._decode_response(200, b"not actually json")


# --------------------------------------------------------------------- #
# call_publisher orchestration (transport injected)                     #
# --------------------------------------------------------------------- #


def test_call_publisher_get_assembles_url_headers_and_decodes() -> None:
    calls: list[dict] = []

    def fake_fetcher(method: str, url: str, headers: dict, body: bytes | None):
        calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        return (200, b'{"projects": []}')

    result = sc.call_publisher(
        "seren-db",
        "GET",
        "/projects",
        body=None,
        api_key="test-key",
        fetcher=fake_fetcher,
    )

    assert result == {"projects": []}
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://api.serendb.com/publishers/seren-db/projects"
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert "Content-Type" not in call["headers"]
    assert call["body"] is None


def test_call_publisher_post_encodes_json_body() -> None:
    captured: list[bytes] = []

    def fake_fetcher(method: str, url: str, headers: dict, body: bytes | None):
        captured.append(body)
        return (201, b'{"id": "prj_x"}')

    result = sc.call_publisher(
        "seren-db",
        "POST",
        "/projects",
        body={"name": "pk-lead-intelligence"},
        api_key="test-key",
        fetcher=fake_fetcher,
    )

    assert result == {"id": "prj_x"}
    assert len(captured) == 1
    # Body is JSON bytes that round-trip through json.loads.
    import json
    assert json.loads(captured[0]) == {"name": "pk-lead-intelligence"}


def test_call_publisher_propagates_publisher_errors() -> None:
    def fake_fetcher(method: str, url: str, headers: dict, body: bytes | None):
        return (401, b'{"error": "unauthorized"}')

    with pytest.raises(sc.PublisherError) as excinfo:
        sc.call_publisher(
            "seren-db",
            "GET",
            "/projects",
            api_key="bad-key",
            fetcher=fake_fetcher,
        )

    assert excinfo.value.status == 401
    assert "unauthorized" in str(excinfo.value)


def test_call_publisher_unwraps_model_routing_gateway_envelope() -> None:
    """Model-routing publishers wrap upstream payloads twice:
    `{"data": {"status": 200, "cost": …, "body": <upstream>}}`.

    Regression for the empty-research bug — every research Note had
    `choices=None` because the adapters were reading the outer wrap.
    The wrapper must hand back the inner `body` so adapters can keep
    their `response.get("choices")` access pattern.
    """

    raw_gateway_payload = (
        b'{"data": {"status": 200, "cost": 0.000139, '
        b'"payment_source": "prepaid_balance", "body": '
        b'{"choices": [{"message": {"content": "PONG"}}], '
        b'"model": "anthropic/claude-sonnet-4-5"}}}'
    )

    def fake_fetcher(method: str, url: str, headers: dict, body: bytes | None):
        return (200, raw_gateway_payload)

    result = sc.call_publisher(
        "seren-models",
        "POST",
        "/chat/completions",
        body={"model": "anthropic/claude-sonnet-4-5", "messages": []},
        api_key="test-key",
        fetcher=fake_fetcher,
    )

    assert result.get("model") == "anthropic/claude-sonnet-4-5"
    choices = result.get("choices") or []
    assert len(choices) == 1
    assert choices[0]["message"]["content"] == "PONG"
