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

import os

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


def test_resolve_api_key_raises_when_neither_is_set_and_auto_register_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """When no env var is set, no `.env` is on disk, and the caller
    opted out of auto-register, `resolve_api_key()` raises so the
    operator gets the actionable error. `auto_register=False` exists
    for callers that want the legacy fail-fast behaviour (CI, tests,
    cron environments where unattended account creation is wrong).
    """

    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SEREN_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="API_KEY"):
        sc.resolve_api_key(auto_register=False, skill_root=tmp_path)


def test_resolve_api_key_error_uses_cowork_settings_connectors_path_when_auto_register_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Issue #792 — PR #790 told Claude Cowork users to run
    `claude mcp add`, a Claude *Code* CLI command. Cowork is the
    desktop app; its install path is Settings > Connectors. The
    error message must call those products by their distinct names
    and route each to its real install path. A future refactor that
    re-conflates them is a P0 regression for the Cowork onboarding
    flow this code path exists to serve.
    """

    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SEREN_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        sc.resolve_api_key(auto_register=False, skill_root=tmp_path)

    message = str(excinfo.value)

    # Cowork desktop block — must cite the Settings > Connectors path
    # and the remote-MCP URL, must not cite `claude mcp add` (wrong
    # product) inside its own block.
    assert "Settings > Connectors" in message
    assert "https://mcp.serendb.com/mcp" in message
    cowork_block_start = message.index("Claude Cowork")
    cowork_block_end = message.index("Claude Code", cowork_block_start)
    assert "claude mcp add" not in message[cowork_block_start:cowork_block_end]

    # Claude Code (CLI) block — separate, owns the `claude mcp add`
    # command. Keeps the recipe for the right product on the right line.
    assert "claude mcp add" in message[cowork_block_end:]

    # /auth/agent fallback is still surfaced for hosts where neither
    # MCP product is reachable (locked-down CI, headless cron boxes).
    assert "https://api.serendb.com/auth/agent" in message


def test_resolve_api_key_reads_seren_api_key_from_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Latent bug from issue #792 audit: SKILL.md tells users to
    paste the key into `<skill-root>/.env`, but the previous
    `resolve_api_key()` only read `os.environ`. A user following the
    docs literally still tripped the cold-start error. This test
    pins the `.env` read so the doc instruction is honored.
    """

    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SEREN_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "# Comment that must be ignored\n"
        "SEREN_API_KEY=from-dotenv-file\n"
        "OP_VAULT=Some Other Var\n",
        encoding="utf-8",
    )

    assert sc.resolve_api_key(skill_root=tmp_path) == "from-dotenv-file"


def test_resolve_api_key_auto_registers_when_no_auth_anywhere(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    """Issue #792 — Jill on Cowork should never see the cold-start
    error. With no env var, no `.env`, and `auto_register=True` (the
    default), `resolve_api_key()` registers a fresh agent account
    via `POST /auth/agent`, writes the key to `<skill-root>/.env`,
    and returns it. A second call reads the now-written `.env`
    instead of re-registering (preserves the no-duplicate-account
    invariant from SKILL.md).
    """

    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SEREN_API_KEY", raising=False)

    fetcher_calls: list[dict] = []

    def fake_fetcher(method: str, url: str, headers: dict, body: bytes | None):
        fetcher_calls.append({"method": method, "url": url, "body": body})
        return (
            201,
            b'{"data": {"agent": {"api_key": "reg-xyz-newly-issued"}}}',
        )

    key = sc.resolve_api_key(skill_root=tmp_path, fetcher=fake_fetcher)

    # (a) Returned key matches the registration response.
    assert key == "reg-xyz-newly-issued"

    # (b) `.env` was created at skill_root with the right line.
    dotenv = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "SEREN_API_KEY=reg-xyz-newly-issued" in dotenv

    # The auto-register call hit the documented endpoint with a JSON
    # body naming this skill. The endpoint contract is part of the
    # public docs at docs.serendb.com/skills.md — pin it so the
    # registration target cannot silently drift.
    assert len(fetcher_calls) == 1
    assert fetcher_calls[0]["method"] == "POST"
    assert fetcher_calls[0]["url"] == "https://api.serendb.com/auth/agent"
    assert b"pk-lead-intelligence" in (fetcher_calls[0]["body"] or b"")

    # One operator-visible warning on stderr so a follow-up audit
    # can find the auto-registration event in the run log.
    captured = capsys.readouterr()
    assert "registered new Seren agent account" in captured.err

    # (c) Second call must NOT re-register. It reads the freshly-
    # written `.env`. This is the duplicate-account guard.
    key_again = sc.resolve_api_key(skill_root=tmp_path, fetcher=fake_fetcher)
    assert key_again == "reg-xyz-newly-issued"
    assert len(fetcher_calls) == 1, "second call must not re-hit /auth/agent"


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


# --------------------------------------------------------------------- #
# Full .env load into os.environ (issue #848)                           #
# --------------------------------------------------------------------- #


def test_load_dotenv_into_environ_loads_all_vars(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Every key in `.env` must land in os.environ — not just
    SEREN_API_KEY. The 1Password path needs OP_SERVICE_ACCOUNT_TOKEN /
    OP_VAULT and Path-A needs SF_* to resolve without the operator
    exporting `.env` by hand.
    """

    for var in ("OP_SERVICE_ACCOUNT_TOKEN", "OP_VAULT", "SF_USERNAME"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / ".env").write_text(
        "# a comment\n"
        "OP_SERVICE_ACCOUNT_TOKEN=tok-123\n"
        "OP_VAULT=PK Salesforce Skill\n"
        '\n'
        'SF_USERNAME="jill@example.com"\n',
        encoding="utf-8",
    )

    loaded = sc.load_dotenv_into_environ([tmp_path / ".env"])

    assert os.environ["OP_SERVICE_ACCOUNT_TOKEN"] == "tok-123"
    assert os.environ["OP_VAULT"] == "PK Salesforce Skill"
    assert os.environ["SF_USERNAME"] == "jill@example.com"
    assert loaded["OP_SERVICE_ACCOUNT_TOKEN"] == "tok-123"


def test_load_dotenv_into_environ_does_not_overwrite_real_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """A variable already set in the real environment wins over `.env`
    — env beats file, matching resolve_api_key's precedence.
    """

    monkeypatch.setenv("OP_VAULT", "real-vault")
    (tmp_path / ".env").write_text("OP_VAULT=from-file\n", encoding="utf-8")

    sc.load_dotenv_into_environ([tmp_path / ".env"])

    assert os.environ["OP_VAULT"] == "real-vault"


def test_load_dotenv_into_environ_loads_first_existing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Search order matters: the first existing `.env` wins and later
    candidates are not consulted. A missing earlier path is skipped.
    """

    monkeypatch.delenv("TOKEN_X", raising=False)
    stable = tmp_path / "stable"
    skillroot = tmp_path / "skillroot"
    stable.mkdir()
    skillroot.mkdir()
    # stable/.env does not exist -> falls through to skillroot/.env.
    (skillroot / ".env").write_text("TOKEN_X=from-skillroot\n", encoding="utf-8")

    sc.load_dotenv_into_environ([stable / ".env", skillroot / ".env"])

    assert os.environ["TOKEN_X"] == "from-skillroot"
