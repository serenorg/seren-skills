from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SKILL_PATH = Path(__file__).resolve().parents[1] / "SKILL.md"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "serendb_schema.sql"

# Add scripts dir to path so we can import agent
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import agent  # noqa: E402


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


# --- Existing fixture tests (unchanged) ---

def test_happy_path_fixture_is_successful() -> None:
    payload = _read_fixture("happy_path.json")
    assert payload["status"] == "ok"
    assert payload["skill"] == "bat-sales-coach"


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


def test_skill_instructions_require_user_stated_dates() -> None:
    content = SKILL_PATH.read_text(encoding="utf-8")

    assert "## Date and Time Rules" in content
    assert "must not assume, compute, or suggest specific dates" in content
    assert "due date (user-stated only, never agent-computed)" in content
    assert "expected close date (user-stated only)" in content
    assert "What is the next behavior for this prospect, and when do you want to do it?" in content


# --- New: Schema file exists and contains required tables ---

def test_schema_file_exists() -> None:
    assert SCHEMA_PATH.exists(), "serendb_schema.sql must exist for first-run bootstrap"


def test_schema_contains_all_required_tables() -> None:
    content = SCHEMA_PATH.read_text(encoding="utf-8")
    required_tables = [
        "prospects",
        "behavior_tasks",
        "behavior_journals",
        "attitude_journals",
        "technique_plans",
        "coaching_sessions",
    ]
    for table in required_tables:
        assert f"{{{{schema_name}}}}.{table}" in content, f"Schema must define table: {table}"


def test_schema_bootstrap_sql_renders_without_error() -> None:
    statements = agent.storage_bootstrap_sql("bat_sales_coach")
    assert len(statements) >= 6, "Schema must produce at least 6 SQL statements (one per table)"
    for stmt in statements:
        assert "{{schema_name}}" not in stmt, "Template variable must be replaced"
        assert "bat_sales_coach" in stmt, "Schema name must appear in rendered SQL"


# --- New: ensure_storage fails fast without API key ---

def test_ensure_storage_fails_without_api_key() -> None:
    import pytest
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(agent.SerenBootstrapError, match="SEREN_API_KEY"):
            agent.ensure_storage({})


# --- New: Behavior table formatting ---

def test_format_behavior_table_empty() -> None:
    assert agent.format_behavior_table([]) == "No behaviors due today."


def test_format_behavior_table_with_rows() -> None:
    behaviors = [
        agent.BehaviorDueToday(
            task_id="t1", prospect_name="Alice", organization="Acme",
            title="Send proposal", due_date="2026-03-31", status="planned",
        ),
    ]
    table = agent.format_behavior_table(behaviors)
    assert "Alice" in table
    assert "Acme" in table
    assert "Send proposal" in table
    assert "|" in table


# --- New: OAuth check returns both providers ---

def test_check_oauth_returns_both_providers() -> None:
    with patch.dict("os.environ", {}, clear=True):
        statuses = agent.check_oauth_providers({})
    providers = {s.provider for s in statuses}
    assert providers == {"microsoft", "google"}
    assert all(not s.authenticated for s in statuses)


def test_check_oauth_detects_token() -> None:
    with patch.dict("os.environ", {"MICROSOFT_OAUTH_TOKEN": "tok123"}, clear=True):
        statuses = agent.check_oauth_providers({})
    ms = next(s for s in statuses if s.provider == "microsoft")
    assert ms.authenticated is True


# --- New: SKILL.md documents Schema Guard bootstrap ---

def test_skill_md_documents_schema_guard_bootstrap() -> None:
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert "## Schema Guard (Mandatory — runs every invoke)" in content
    assert "bat-sales-coach" in content
    assert "bat_sales_coach" in content


# --- New: run_once returns required keys ---

def test_run_once_returns_error_without_storage() -> None:
    with patch.dict("os.environ", {}, clear=True):
        result = agent.run_once({}, dry_run=True)
    assert result["status"] == "error"
    assert result["error_code"] == "storage_bootstrap_failed"


# --- Persuasion-safety guardrails (arXiv 2507.13919) ---


def test_guardrail_g1_distress_escalation_rule() -> None:
    """G1: SKILL.md must contain a distress escalation rule that halts the loop and provides crisis resources."""
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert "## Distress Escalation Rule" in content
    assert "988" in content, "Must reference the 988 Suicide and Crisis Lifeline"
    assert "Stop the coaching loop" in content


def test_guardrail_g2_mandatory_sequence_after_behavior() -> None:
    """G2: After Behavior Readiness Check passes, the loop must transition Behavior → Attitude → Technique with no skip prompt."""
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert "**MANDATORY SEQUENCE RULE**" in content
    assert "Do not offer to skip or end the session between Behavior and Attitude" in content


def test_guardrail_g3_future_loop_capped() -> None:
    """G3: 'Can you tell the future?' loop must be capped at 2 cycles."""
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert "at most **2 times**" in content
    assert "That is okay" in content


def test_guardrail_g4_no_body_part_probing() -> None:
    """G4: Must not direct users to locate sensations in specific body parts."""
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert "Do not direct them to locate sensations in specific body parts" in content
    # The old somatic question must be gone
    assert "where that score is felt in the body" not in content.lower()


def test_guardrail_g5_accuracy_anchor() -> None:
    """G5: Supportive feedback must include a factual observation."""
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert "concrete, factual observation" in content
    assert "acknowledge it honestly" in content


def test_guardrail_g6_research_transparency() -> None:
    """G6: Research sources must not be hidden by default."""
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert "Keep research hidden" not in content
    assert "Briefly mention the general source area" in content


def test_guardrail_g7_attitude_trend_monitoring() -> None:
    """G7: Must detect declining attitude scores across sessions."""
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert "## Attitude Trend Monitoring" in content
    assert "3 or more consecutive sessions" in content


# --- Canonical pipeline_stage contract (issue #446) ---

CANONICAL_STAGES = (
    "lead", "prospecting", "discovery", "demo_completed",
    "proposal", "closed_won", "closed_lost",
)


def test_canonical_pipeline_stages_documented_in_skill_md() -> None:
    """SKILL.md must declare the canonical 7-value pipeline_stage set."""
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert "## Canonical Pipeline Stages" in content
    for stage in CANONICAL_STAGES:
        assert stage in content, f"Canonical stage missing from SKILL.md: {stage}"


def test_skill_md_schema_guard_enforces_canonical_stages() -> None:
    """SKILL.md Schema Guard must contain CHECK constraint and migration mappings for all known legacy variants."""
    content = SKILL_PATH.read_text(encoding="utf-8")
    assert "prospects_pipeline_stage_check" in content
    assert "behavior_tasks_pipeline_stage_check" in content
    for variant, canonical in [
        ("'Prospecting'", "'prospecting'"),
        ("'closed-lost'", "'closed_lost'"),
        ("'Intro Pending','Discovery / Demo','Meeting/Discovery'", "'discovery'"),
        ("'Proposal / Pricing','Grant Application'", "'proposal'"),
    ]:
        assert variant in content, f"SKILL.md missing legacy variant: {variant}"
        assert canonical in content, f"SKILL.md missing canonical target: {canonical}"


def test_serendb_schema_enforces_canonical_stages() -> None:
    """serendb_schema.sql must apply the same CHECK constraint and idempotent migration."""
    content = SCHEMA_PATH.read_text(encoding="utf-8")
    assert content.count("CHECK (pipeline_stage IS NULL OR pipeline_stage IN") >= 4
    assert "prospects_pipeline_stage_check" in content
    assert "behavior_tasks_pipeline_stage_check" in content
    for variant in ("'Prospecting'", "'closed-lost'", "'Intro Pending'",
                    "'Discovery / Demo'", "'Proposal / Pricing'",
                    "'Meeting/Discovery'", "'Grant Application'"):
        assert variant in content, f"Schema migration missing variant: {variant}"
