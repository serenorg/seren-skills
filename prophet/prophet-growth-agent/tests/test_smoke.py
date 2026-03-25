from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


FIXTURE_DIR = Path(__file__).parent / "fixtures"
SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent.py"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "serendb_schema.sql"


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("prophet_growth_agent", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_happy_path_fixture_is_successful() -> None:
    payload = _read_fixture("happy_path.json")
    assert payload["status"] == "ok"
    assert payload["skill"] == "prophet-growth-agent"


def test_connector_failure_fixture_has_error_code() -> None:
    payload = _read_fixture("connector_failure.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "connector_failure"


def test_policy_violation_fixture_has_error_code() -> None:
    payload = _read_fixture("policy_violation.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "policy_violation"


def test_dry_run_fixture_blocks_live_execution() -> None:
    payload = _read_fixture("dry_run_guard.json")
    assert payload["dry_run"] is True
    assert payload["blocked_action"] == "live_execution"


def test_validate_prophet_access_requires_bearer_token_header(monkeypatch) -> None:
    agent = _load_agent_module()
    captured = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "data": {
                        "viewer": {
                            "walletBalance": {
                                "availableCents": 0,
                                "totalCents": 0,
                                "safeAddress": "0xabc",
                                "safeDeployed": False,
                            }
                        }
                    }
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout=30):
        captured["authorization"] = request.get_header("Authorization")
        return DummyResponse()

    monkeypatch.setattr(agent.urllib.request, "urlopen", fake_urlopen)
    result = agent.validate_prophet_access({"secrets": {"PROPHET_SESSION_TOKEN": "privy-jwt"}})

    assert captured["authorization"] == "Bearer privy-jwt"
    assert result["status"] == "ok"
    assert result["required_header"] == "Authorization: Bearer <PROPHET_SESSION_TOKEN>"


def test_ensure_storage_bootstraps_schema_when_seren_resources_are_missing(monkeypatch) -> None:
    agent = _load_agent_module()
    executed = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement):
            executed.append(statement)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def cursor(self):
            return FakeCursor()

        def commit(self):
            executed.append("COMMIT")

    monkeypatch.setattr(
        agent,
        "resolve_or_create_serendb_target",
        lambda api_key, project_name, database_name, region: SimpleNamespace(
            project_id="proj_123",
            branch_id="branch_123",
            branch_name="main",
            database_name=database_name,
            connection_string="postgresql://example/prophet",
            project_name=project_name,
            created_project=True,
            created_database=True,
        ),
    )
    monkeypatch.setattr(agent, "psycopg_connect", lambda dsn: FakeConnection())

    result = agent.ensure_storage(
        {
            "storage": {
                "auto_bootstrap": True,
                "project_name": "prophet",
                "database_name": "prophet",
                "schema_name": "prophet_growth_agent",
                "region": "aws-us-east-2",
            },
            "secrets": {"SEREN_API_KEY": "sb_test"},
        }
    )

    assert result["status"] == "ok"
    assert result["project_name"] == "prophet"
    assert result["auto_provisioned"] is True
    assert result["created_project"] is True
    assert result["created_database"] is True
    assert result["statements_executed"] >= 6
    assert any("CREATE SCHEMA IF NOT EXISTS prophet_growth_agent" in stmt for stmt in executed)


def test_resolve_testnet_config_returns_faucet_when_enabled(monkeypatch) -> None:
    agent = _load_agent_module()
    monkeypatch.delenv("PROPHET_TESTNET_MODE", raising=False)

    assert agent.resolve_testnet_config({}) is None
    assert agent.resolve_testnet_config({"testnet": {"enabled": False}}) is None

    result = agent.resolve_testnet_config({"testnet": {"enabled": True}})
    assert result is not None
    assert result["enabled"] is True
    assert result["usdc_faucet"] == "0xa0f2da5e260486895d73086dd98af09c25dc2883c6ac96025a688f855c180d06"
    assert result["base_url"] == "https://testnet.prophetmarket.ai"


def test_storage_bootstrap_sql_reads_checked_in_schema_file() -> None:
    agent = _load_agent_module()

    statements = agent.storage_bootstrap_sql("prophet_growth_agent")

    assert SCHEMA_PATH.exists()
    assert any("CREATE TABLE IF NOT EXISTS prophet_growth_agent.engagement_events" in stmt for stmt in statements)
    assert any("CREATE TABLE IF NOT EXISTS prophet_growth_agent.checkin_recommendations" in stmt for stmt in statements)
