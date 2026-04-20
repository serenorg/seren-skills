"""Critical tests for the push_to_* functions on the reference leaf.

One reference leaf covers the push-contract tests. The other 54 leaves share
the same template, so per-leaf duplication of these tests would be pure
DRY-violation and adds no bug-catching power.

Scope:
  1. Each push is a no-op when its config block is absent.
  2. Each push validates required config keys and rejects missing ones.
  3. Each push invokes the right publisher / auth path when config is present
     (GatewayClient + snowflake.connector.connect are stubbed).
  4. PII redaction runs on the Snowflake structured payload before ingest.
  5. SharePoint URLs / Asana responses are never logged at INFO.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
_AGENT_PATH = HERE.parent / "scripts" / "agent.py"


def _load_agent():
    mod_name = f"family_office_{HERE.parent.name.replace('-', '_')}_agent_sinks"
    spec = importlib.util.spec_from_file_location(mod_name, _AGENT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest() -> dict:
    return {
        "artifact_id": "artifact:cpa-tax-package-checklist-deadbeef0000",
        "skill": "cpa-tax-package-checklist",
        "pillar": "complexity-management",
        "artifact_name": "CPA Tax Package Checklist",
        "artifact_version": 1,
        "created_at": "2026-04-20T16:00:00+00:00",
        "content_hash": "deadbeef" * 8,
        "out_dir": "/tmp/irrelevant",
    }


def _answers() -> dict:
    return {"tax_year": "2026", "cpa_firm": "Johnson & Co.", "entities_in_scope": "Smith Trust"}


# ── No-op when config absent ────────────────────────────────────────────

def test_sharepoint_push_noop_when_config_absent() -> None:
    agent = _load_agent()
    assert agent.push_to_sharepoint(_manifest(), config=None) is None
    assert agent.push_to_sharepoint(_manifest(), config={}) is None
    assert agent.push_to_sharepoint(_manifest(), config={"other": "thing"}) is None


def test_asana_push_noop_when_config_absent() -> None:
    agent = _load_agent()
    assert agent.push_to_asana(_manifest(), _answers(), config=None) is None
    assert agent.push_to_asana(_manifest(), _answers(), config={}) is None


def test_snowflake_push_noop_when_config_absent() -> None:
    agent = _load_agent()
    assert agent.push_to_snowflake(_manifest(), _answers(), config=None) is None
    assert agent.push_to_snowflake(_manifest(), _answers(), config={}) is None


# ── Required-key validation (negative tests) ────────────────────────────

def test_sharepoint_push_rejects_missing_required_keys() -> None:
    agent = _load_agent()
    with pytest.raises(ValueError, match="sharepoint config missing"):
        agent.push_to_sharepoint(
            _manifest(), config={"sharepoint": {"site_id": "s"}}
        )


def test_asana_push_rejects_missing_required_keys() -> None:
    agent = _load_agent()
    with pytest.raises(ValueError, match="asana config missing"):
        agent.push_to_asana(
            _manifest(), _answers(), config={"asana": {"workspace_gid": "1"}}
        )


def test_snowflake_push_rejects_missing_required_keys() -> None:
    agent = _load_agent()
    with pytest.raises(ValueError, match="snowflake config missing"):
        agent.push_to_snowflake(
            _manifest(),
            _answers(),
            config={"snowflake": {"account": "a", "user": "u"}},
        )


# ── Happy path with stubbed transport ───────────────────────────────────

def _stub_gateway(monkeypatch, module, captured: list) -> None:
    """Replace GatewayClient with one that records calls instead of hitting
    the network. SEREN_API_KEY still must be present (so we also stub the
    env var)."""
    monkeypatch.setenv("SEREN_API_KEY", "test-key")

    class _StubGateway:
        def __init__(self, **kwargs) -> None:  # noqa: ARG002
            pass

        def call_publisher(self, publisher, method, path, *, body=None):
            captured.append(
                {"publisher": publisher, "method": method, "path": path, "body": body}
            )
            return {"ok": True, "returned_url": "https://redacted.example/never-log"}

    monkeypatch.setattr(module, "GatewayClient", _StubGateway)


def test_sharepoint_push_calls_microsoft_sharepoint_with_upload_body(
    monkeypatch, tmp_path
) -> None:
    agent = _load_agent()

    # Real artifact on disk so the push can read it.
    out = tmp_path / "out"
    out.mkdir()
    (out / "artifact.md").write_text("# Artifact body\n", encoding="utf-8")
    manifest = _manifest()
    manifest["out_dir"] = str(out)

    calls: list[dict] = []
    _stub_gateway(monkeypatch, agent, calls)

    result = agent.push_to_sharepoint(
        manifest,
        config={
            "sharepoint": {
                "site_id": "contoso.sharepoint.com,aaa,bbb",
                "drive_id": "b!abc",
                "folder_path": "/Seren/family-office",
            }
        },
    )

    assert result is not None
    assert result["publisher"] == "microsoft-sharepoint"
    assert len(calls) == 1
    call = calls[0]
    assert call["publisher"] == "microsoft-sharepoint"
    assert call["method"] == "POST"
    assert call["path"] == "/files/upload"
    body = call["body"]
    assert body["site_id"] == "contoso.sharepoint.com,aaa,bbb"
    assert body["drive_id"] == "b!abc"
    assert body["path"].startswith("/Seren/family-office/cpa-tax-package-checklist/")
    assert body["path"].endswith("/artifact.md")
    assert body["content"] == "# Artifact body\n"
    assert body["content_type"].startswith("text/markdown")


def test_asana_push_creates_task_with_expected_fields(monkeypatch) -> None:
    agent = _load_agent()
    calls: list[dict] = []
    _stub_gateway(monkeypatch, agent, calls)

    result = agent.push_to_asana(
        _manifest(),
        _answers(),
        config={
            "asana": {
                "workspace_gid": "WS1",
                "project_gid": "P1",
                "assignee_gid": "U1",
            }
        },
    )
    assert result is not None
    assert result["publisher"] == "asana"
    assert len(calls) == 1
    call = calls[0]
    assert call["publisher"] == "asana"
    assert call["method"] == "POST"
    assert call["path"] == "/tasks"
    data = call["body"]["data"]
    assert data["workspace"] == "WS1"
    assert data["projects"] == ["P1"]
    assert data["assignee"] == "U1"
    assert "CPA Tax Package Checklist" in data["name"]
    assert "artifact:cpa-tax-package-checklist" in data["notes"]


def test_snowflake_push_insert_parameters_and_redacts_pii(monkeypatch) -> None:
    agent = _load_agent()

    captured: dict = {}

    class _StubCursor:
        def __init__(self) -> None:
            self.sfqid = "query-id-xyz"

        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params

        def close(self):
            pass

    class _StubConn:
        def __init__(self, **kwargs):
            captured["connect_kwargs"] = kwargs

        def cursor(self):
            return _StubCursor()

        def commit(self):
            captured["committed"] = True

        def close(self):
            captured["closed"] = True

    # Stub the snowflake.connector module before the push function imports it.
    stub_connector = SimpleNamespace(connect=lambda **kw: _StubConn(**kw))
    stub_module = SimpleNamespace(connector=stub_connector)
    monkeypatch.setitem(sys.modules, "snowflake", stub_module)
    monkeypatch.setitem(sys.modules, "snowflake.connector", stub_connector)

    # Include a PII-bearing answer to verify redaction before ingest.
    answers_with_pii = dict(_answers())
    answers_with_pii["principal_ssn"] = "123-45-6789"
    answers_with_pii["trust_ein"] = "12-3456789"

    result = agent.push_to_snowflake(
        _manifest(),
        answers_with_pii,
        config={
            "snowflake": {
                "account": "rendero.us-east-1",
                "user": "seren_agent",
                "warehouse": "FO_WH",
                "database": "FO_DB",
                "schema": "SEREN",
                "role": "FO_WRITER",
                # authenticator defaults to externalbrowser
            }
        },
    )

    assert result is not None
    assert result["publisher"] == "snowflake"
    assert result["query_id"] == "query-id-xyz"

    # External-browser auth was selected by default (no password env var).
    connect_kwargs = captured["connect_kwargs"]
    assert connect_kwargs["authenticator"] == "externalbrowser"
    assert connect_kwargs["user"] == "seren_agent"
    assert connect_kwargs["warehouse"] == "FO_WH"
    assert connect_kwargs["role"] == "FO_WRITER"
    assert "password" not in connect_kwargs
    assert "private_key_file" not in connect_kwargs

    # SQL is parameterized; structured_payload redacts PII before it leaves
    # the process.
    sql = captured["sql"]
    assert "INSERT INTO FO_ARTIFACTS" in sql
    assert "PARSE_JSON(%s)" in sql
    assert "123-45-6789" not in sql  # never inlined

    params = captured["params"]
    structured_json = params[-1]  # last param is structured_payload
    payload = json.loads(structured_json)
    # Raw SSN/EIN values must have been redacted.
    assert "123-45-6789" not in structured_json
    assert "12-3456789" not in structured_json
    # The PII-bearing keys themselves are also redacted.
    assert payload["inputs"]["principal_ssn"] == "<redacted>"
    assert payload["inputs"]["trust_ein"] == "<redacted>"
    # Non-PII inputs are preserved verbatim.
    assert payload["inputs"]["tax_year"] == "2026"

    assert captured["committed"] is True
    assert captured["closed"] is True


def test_snowflake_password_auth_requires_env_var(monkeypatch) -> None:
    agent = _load_agent()
    monkeypatch.delenv("SNOWFLAKE_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="SNOWFLAKE_PASSWORD"):
        agent.push_to_snowflake(
            _manifest(),
            _answers(),
            config={
                "snowflake": {
                    "account": "a",
                    "user": "u",
                    "warehouse": "w",
                    "database": "d",
                    "schema": "s",
                    "authenticator": "snowflake",
                }
            },
        )


def test_snowflake_jwt_auth_requires_private_key_env_var(monkeypatch) -> None:
    agent = _load_agent()
    monkeypatch.delenv("SNOWFLAKE_PRIVATE_KEY_PATH", raising=False)
    with pytest.raises(ValueError, match="SNOWFLAKE_PRIVATE_KEY_PATH"):
        agent.push_to_snowflake(
            _manifest(),
            _answers(),
            config={
                "snowflake": {
                    "account": "a",
                    "user": "u",
                    "warehouse": "w",
                    "database": "d",
                    "schema": "s",
                    "authenticator": "snowflake_jwt",
                }
            },
        )


# ── Logging hygiene ─────────────────────────────────────────────────────

def test_sharepoint_push_never_logs_returned_url_at_info_level(
    monkeypatch, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    agent = _load_agent()
    out = tmp_path / "out"
    out.mkdir()
    (out / "artifact.md").write_text("body", encoding="utf-8")
    manifest = _manifest()
    manifest["out_dir"] = str(out)

    calls: list[dict] = []
    _stub_gateway(monkeypatch, agent, calls)

    with caplog.at_level(logging.INFO, logger=f"family_office.{agent.SKILL_NAME}"):
        agent.push_to_sharepoint(
            manifest,
            config={
                "sharepoint": {
                    "site_id": "s",
                    "drive_id": "d",
                    "folder_path": "/Seren/family-office",
                }
            },
        )
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "redacted.example" not in joined
    assert "returned_url" not in joined
