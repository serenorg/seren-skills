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
    spec = importlib.util.spec_from_file_location("prophet_market_seeder_agent", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_happy_path_fixture_is_successful() -> None:
    payload = _read_fixture("happy_path.json")
    assert payload["status"] == "ok"
    assert payload["skill"] == "prophet-market-seeder"


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
        captured["content_type"] = request.get_header("Content-type")
        return DummyResponse()

    monkeypatch.setattr(agent.urllib.request, "urlopen", fake_urlopen)
    result = agent.validate_prophet_access({"secrets": {"PROPHET_SESSION_TOKEN": "privy-jwt"}})

    assert captured["authorization"] == "Bearer privy-jwt"
    assert captured["content_type"] == "application/json"
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
                "schema_name": "prophet_market_seeder",
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
    assert any("CREATE SCHEMA IF NOT EXISTS prophet_market_seeder" in stmt for stmt in executed)


def test_storage_bootstrap_sql_reads_checked_in_schema_file() -> None:
    agent = _load_agent_module()

    statements = agent.storage_bootstrap_sql("prophet_market_seeder")

    assert SCHEMA_PATH.exists()
    assert any("CREATE TABLE IF NOT EXISTS prophet_market_seeder.sessions" in stmt for stmt in statements)
    assert any("CREATE TABLE IF NOT EXISTS prophet_market_seeder.artifacts" in stmt for stmt in statements)


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


def test_setup_without_seren_api_key_points_user_to_docs(monkeypatch) -> None:
    agent = _load_agent_module()
    monkeypatch.delenv("SEREN_API_KEY", raising=False)

    result = agent.run_once(
        {
            "dry_run": True,
            "inputs": {"command": "setup", "strict_mode": True},
            "secrets": {"PROPHET_SESSION_TOKEN": "privy-jwt"},
            "storage": {"auto_bootstrap": True},
        },
        dry_run=True,
    )

    assert result["status"] == "error"
    assert result["error_code"] == "missing_seren_api_key"
    assert result["details"]["docs_url"] == "https://docs.serendb.com/skills.md"


# ---------------------------------------------------------------------------
# Pipeline transform tests
# ---------------------------------------------------------------------------


def test_generate_market_candidates_respects_limit() -> None:
    agent = _load_agent_module()
    ctx = agent.PipelineContext(
        session_id="s1", run_id="r1", command="run", dry_run=True,
        referral_code="TEST", candidate_limit=5, submit_limit=2,
        strict_mode=True, token="tok",
    )
    candidates = agent.generate_market_candidates(ctx)
    assert len(candidates) == 5
    assert all(c.question.endswith("?") for c in candidates)
    assert all(c.candidate_id for c in candidates)


def test_score_sorts_by_descending_score() -> None:
    agent = _load_agent_module()
    candidates = [
        agent.MarketCandidate(candidate_id="a", category="Crypto", question="Will BTC hit 100K by Dec?"),
        agent.MarketCandidate(candidate_id="b", category="Crypto", question="Will ETH hit 5K by Dec?"),
        agent.MarketCandidate(candidate_id="c", category="Politics", question="Will new law pass by June?"),
    ]
    scored = agent.score_market_candidates(candidates)
    assert scored[0].score >= scored[1].score >= scored[2].score
    assert all(c.score > 0 for c in scored)


def test_filter_dedup_and_limit() -> None:
    agent = _load_agent_module()
    candidates = [
        agent.MarketCandidate(candidate_id="a", category="Crypto", question="Will BTC hit 100K?", score=0.9),
        agent.MarketCandidate(candidate_id="b", category="Sports", question="Will Lakers win?", score=0.8),
        agent.MarketCandidate(candidate_id="c", category="Health", question="Will WHO act?", score=0.7),
    ]
    filtered = agent.filter_market_candidates(candidates, submit_limit=2, recent_titles=["will btc hit 100k?"])
    assert len(filtered) == 2
    assert filtered[0].candidate_id == "b"
    assert filtered[1].candidate_id == "c"


def test_submit_batch_dry_run_skips_api(monkeypatch) -> None:
    agent = _load_agent_module()
    ctx = agent.PipelineContext(
        session_id="s1", run_id="r1", command="run", dry_run=True,
        referral_code="TEST", candidate_limit=5, submit_limit=2,
        strict_mode=True, token="tok",
    )
    candidates = [
        agent.MarketCandidate(candidate_id="a", category="Crypto", question="Will BTC moon?", score=0.9),
    ]
    api = agent.ProphetApi("fake-token")
    results = agent.submit_market_batch(ctx, candidates, api)
    assert len(results) == 1
    assert results[0].status == "dry_run_skipped"


def test_submit_batch_live_calls_initiate_market(monkeypatch) -> None:
    agent = _load_agent_module()
    ctx = agent.PipelineContext(
        session_id="s1", run_id="r1", command="run", dry_run=False,
        referral_code="TEST", candidate_limit=5, submit_limit=2,
        strict_mode=True, token="tok",
    )
    candidates = [
        agent.MarketCandidate(candidate_id="a", category="Crypto", question="Will BTC moon?", score=0.9),
    ]

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps({"data": {"initiateMarket": {
                "isValid": True, "suggestion": None,
                "title": "Will BTC moon?", "resolutionDate": "2026-12-31",
                "resolutionRules": "Resolves YES if BTC > 200K",
            }}}).encode("utf-8")

    monkeypatch.setattr(agent.urllib.request, "urlopen", lambda req, timeout=30: FakeResponse())
    api = agent.ProphetApi("fake-token")
    results = agent.submit_market_batch(ctx, candidates, api)
    assert len(results) == 1
    assert results[0].status == "accepted"
    assert results[0].payload["title"] == "Will BTC moon?"


def test_run_once_pipeline_dry_run_returns_full_report(monkeypatch) -> None:
    agent = _load_agent_module()
    monkeypatch.delenv("SEREN_API_KEY", raising=False)

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps({"data": {"viewer": {"walletBalance": {
                "availableCents": 0, "totalCents": 0,
                "safeAddress": "0xabc", "safeDeployed": True,
            }}}}).encode("utf-8")

    class FakeCursor:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def execute(self, *a):
            pass
        def fetchall(self):
            return []

    class FakeConnection:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def cursor(self):
            return FakeCursor()
        def commit(self):
            pass

    monkeypatch.setattr(agent.urllib.request, "urlopen", lambda req, timeout=30, data=None: FakeResponse())
    monkeypatch.setattr(agent, "psycopg_connect", lambda dsn: FakeConnection())
    monkeypatch.setattr(
        agent, "resolve_or_create_serendb_target",
        lambda api_key, project_name, database_name, region: SimpleNamespace(
            project_id="p1", branch_id="b1", branch_name="main",
            database_name=database_name, connection_string="postgresql://example/prophet",
            project_name=project_name, created_project=False, created_database=False,
        ),
    )

    result = agent.run_once(
        {
            "inputs": {"command": "run", "candidate_limit": 3, "submit_limit": 2, "strict_mode": True, "referral_code": "TEST"},
            "storage": {"auto_bootstrap": True, "schema_name": "prophet_market_seeder"},
            "secrets": {"SEREN_API_KEY": "sb_test", "PROPHET_SESSION_TOKEN": "privy-jwt"},
        },
        dry_run=True,
    )

    assert result["status"] == "ok"
    assert result["command"] == "run"
    assert result["dry_run"] is True
    assert result["pipeline"]["candidates_generated"] == 3
    assert result["pipeline"]["candidates_filtered"] <= 2
    assert result["pipeline"]["submissions_skipped"] == result["pipeline"]["candidates_filtered"]
    assert len(result["submissions"]) > 0
    assert all(s["status"] == "dry_run_skipped" for s in result["submissions"])
