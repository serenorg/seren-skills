"""Tests for team memory system (issue #371).

Covers: structured memory distillation, proactive resurfacing, stale
validation, new command modes, engagement events, and backward compatibility.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPT_DIR / "agent.py"
TM_PATH = SCRIPT_DIR / "team_memory.py"


def _load_team_memory():
    spec = importlib.util.spec_from_file_location("team_memory", TM_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def tm():
    return _load_team_memory()


def _load_agent():
    spec = importlib.util.spec_from_file_location("knowledge_agent", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def _base_config(command: str) -> dict:
    return {
        "current_date": "2026-04-04",
        "dry_run": True,
        "inputs": {
            "access_scope": "team",
            "asana_sync_enabled": True,
            "command": command,
            "department": "investments",
            "interview_mode": "freeform",
            "organization_name": "Rendero Trust",
            "query_text": "tax strategy",
            "requester_id": "user-123",
            "reward_base_usd": 100,
            "reward_per_retrieval_usd": 1,
            "sharepoint_sync_enabled": True,
            "top_k": 5,
        },
        "storage_records": {
            "briefs": [],
            "knowledge_entries": [],
            "retrieval_events": [],
            "reward_audits": [],
            "transcripts": [],
            "memory_objects": [],
            "memory_links": [],
            "memory_validations": [],
            "memory_subscriptions": [],
            "memory_nudges": [],
            "engagement_events": [],
        },
        "sharepoint_context": [],
        "asana_context": [],
        "documents": [],
        "interview_transcript": [],
    }


# --- Structured memory distillation ---


class TestClassifyMemoryType:

    def test_decision_detected(self, tm) -> None:
        assert tm.classify_memory_type("We decided to go with the new fund structure") == "decision"

    def test_assumption_detected(self, tm) -> None:
        assert tm.classify_memory_type("Assuming the tax rate stays at 21%") == "assumption"

    def test_risk_detected(self, tm) -> None:
        assert tm.classify_memory_type("The main risk is currency exposure in the European portfolio") == "risk"

    def test_open_question_detected(self, tm) -> None:
        assert tm.classify_memory_type("Need to find out whether the LP has capacity?") == "open_question"

    def test_commitment_detected(self, tm) -> None:
        assert tm.classify_memory_type("We committed to delivering the report by Friday deadline") == "commitment"

    def test_fallback_to_source_claim(self, tm) -> None:
        assert tm.classify_memory_type("The portfolio returned 12% last year") == "source_claim"


class TestDistillStructuredMemories:

    def test_entries_become_memory_objects(self, tm) -> None:
        entries = [
            {"id": "e1", "summary": "We decided to use QSBS", "content": "We decided to use QSBS", "topic": "tax", "owner_id": "u1", "source": "interview"},
            {"id": "e2", "summary": "Risk of audit exposure", "content": "Risk of audit exposure", "topic": "tax", "owner_id": "u1", "source": "sharepoint"},
        ]
        request = {"topic": "tax", "requester_id": "u1", "access_scope": "team", "organization_name": "Test", "department": "ops", "current_date": "2026-04-04"}
        memories = tm.distill_structured_memories(entries, request)
        assert len(memories) == 2
        assert memories[0]["memory_type"] == "decision"
        assert memories[1]["memory_type"] == "risk"
        assert all("id" in m and "key_claim" in m and "validity_status" in m for m in memories)
        # Backward compat: original entry fields preserved
        assert memories[0]["original_entry_id"] == "e1"


# --- Proactive resurfacing ---


class TestResurfacing:

    def test_relevant_memories_surface(self, tm) -> None:
        memories = [
            tm.build_memory_object(memory_type="decision", key_claim="Use QSBS for tax optimization", subject="tax strategy", current_date="2026-03-01"),
            tm.build_memory_object(memory_type="risk", key_claim="Currency exposure in European bonds", subject="bonds", current_date="2026-03-01"),
        ]
        results = tm.find_memories_to_resurface(memories, "tax strategy planning", "2026-04-04", threshold=0.1)
        assert len(results) >= 1
        assert any("tax" in r.get("key_claim", "").lower() for r in results)

    def test_retired_memories_excluded(self, tm) -> None:
        mem = tm.build_memory_object(memory_type="decision", key_claim="Old decision", subject="tax", current_date="2026-01-01")
        mem["validity_status"] = "retired"
        results = tm.find_memories_to_resurface([mem], "tax", "2026-04-04")
        assert len(results) == 0


# --- Stale memory validation ---


class TestStaleValidation:

    def test_overdue_memories_found(self, tm) -> None:
        mem = tm.build_memory_object(memory_type="assumption", key_claim="Tax rate stays at 21%", current_date="2026-01-01")
        mem["review_cadence_days"] = 30
        mem["last_validated_at"] = "2026-01-01"
        stale = tm.find_stale_memories([mem], "2026-04-04")
        assert len(stale) == 1
        assert stale[0]["days_overdue"] > 60
        assert "still valid" in stale[0]["validation_prompt"].lower()

    def test_validate_confirm(self, tm) -> None:
        mem = tm.build_memory_object(memory_type="assumption", key_claim="Rate is 21%", current_date="2026-01-01")
        updated, validation = tm.validate_memory(mem, confirmed=True, validator_id="user-1", current_date="2026-04-04")
        assert updated["validity_status"] == "active"
        assert updated["last_validated_at"] == "2026-04-04"
        assert validation["action"] == "confirmed"

    def test_validate_revise(self, tm) -> None:
        mem = tm.build_memory_object(memory_type="assumption", key_claim="Rate is 21%", current_date="2026-01-01")
        updated, validation = tm.validate_memory(mem, confirmed=False, revised_claim="Rate is now 25%", current_date="2026-04-04")
        assert updated["key_claim"] == "Rate is now 25%"
        assert validation["action"] == "revised"

    def test_validate_retire(self, tm) -> None:
        mem = tm.build_memory_object(memory_type="assumption", key_claim="Old assumption", current_date="2026-01-01")
        updated, validation = tm.validate_memory(mem, confirmed=False, current_date="2026-04-04")
        assert updated["validity_status"] == "retired"
        assert validation["action"] == "retired"


# --- Digest and pre-meeting brief ---


class TestDigestAndBrief:

    def test_digest_includes_stale_and_open_questions(self, tm) -> None:
        memories = [
            tm.build_memory_object(memory_type="open_question", key_claim="LP capacity?", current_date="2026-04-01"),
            tm.build_memory_object(memory_type="assumption", key_claim="Rate stays", current_date="2026-01-01"),
        ]
        memories[1]["last_validated_at"] = "2026-01-01"
        memories[1]["review_cadence_days"] = 30
        digest = tm.generate_memory_digest(memories, "2026-04-04")
        assert digest["total_memories"] == 2
        assert len(digest["open_questions"]) == 1
        assert len(digest["stale_memories"]) == 1

    def test_pre_meeting_brief_surfaces_decisions(self, tm) -> None:
        memories = [
            tm.build_memory_object(memory_type="decision", key_claim="Approved QSBS strategy for tax", subject="tax planning", current_date="2026-03-01"),
            tm.build_memory_object(memory_type="risk", key_claim="Regulatory risk on new fund", subject="fund launch", current_date="2026-03-15"),
        ]
        brief = tm.generate_pre_meeting_brief(memories, "tax planning review", "2026-04-04")
        assert brief["meeting_topic"] == "tax planning review"
        assert len(brief["prior_decisions"]) >= 1


# --- Agent integration: new modes produce output ---


class TestAgentNewModes:

    def test_capture_produces_memory_objects(self, monkeypatch) -> None:
        agent = _load_agent()
        monkeypatch.setattr(agent, "fire_reward_webhook", lambda **_: {"status": "skipped"})
        config = _base_config("capture")
        config["interview_transcript"] = [
            {"speaker": "user", "text": "We decided to restructure the trust before Q4."},
        ]
        result = agent.run_once(config, dry_run=True)
        assert result["status"] == "ok"
        assert len(result["memory_objects"]) >= 1
        assert result["memory_objects"][0]["memory_type"] == "decision"
        # Backward compat: knowledge_entries still produced
        assert len(result["knowledge_entries"]) >= 1

    def test_memory_digest_mode(self, monkeypatch) -> None:
        agent = _load_agent()
        monkeypatch.setattr(agent, "fire_reward_webhook", lambda **_: {"status": "skipped"})
        config = _base_config("memory_digest")
        result = agent.run_once(config, dry_run=True)
        assert result["normalized_request"]["mode"] == "memory_digest"
        assert "total_memories" in result["team_memory_result"]

    def test_validate_memory_mode(self, monkeypatch) -> None:
        agent = _load_agent()
        monkeypatch.setattr(agent, "fire_reward_webhook", lambda **_: {"status": "skipped"})
        config = _base_config("validate_memory")
        result = agent.run_once(config, dry_run=True)
        assert result["normalized_request"]["mode"] == "validate_memory"
        assert "stale_memories" in result["team_memory_result"]

    def test_watch_topic_creates_subscription(self, monkeypatch) -> None:
        agent = _load_agent()
        monkeypatch.setattr(agent, "fire_reward_webhook", lambda **_: {"status": "skipped"})
        config = _base_config("watch_topic")
        result = agent.run_once(config, dry_run=True)
        assert result["normalized_request"]["mode"] == "watch_topic"
        assert "subscription" in result["team_memory_result"]
        assert len(result["storage_state"]["memory_subscriptions"]) == 1


# --- Backward compatibility ---


class TestBackwardCompat:

    def test_retrieve_still_works(self, monkeypatch) -> None:
        agent = _load_agent()
        monkeypatch.setattr(agent, "fire_reward_webhook", lambda **_: {"status": "sent", "transaction_id": "tx"})
        config = _base_config("recall")
        config["storage_records"]["knowledge_entries"] = [
            {
                "id": "entry-1", "organization_name": "Rendero Trust",
                "department": "investments", "topic": "tax strategy",
                "title": "QSBS planning", "summary": "Use QSBS.", "content": "Use QSBS.",
                "owner_id": "user-999", "access_scope": "public",
                "created_at": "2026-03-20", "updated_at": "2026-03-20", "stale_after_days": 30,
            },
        ]
        result = agent.run_once(config, dry_run=True)
        assert result["status"] == "ok"
        assert result["normalized_request"]["mode"] == "retrieve"
        assert len(result["retrieval_results"]["records"]) >= 1

    def test_brief_still_works(self) -> None:
        agent = _load_agent()
        config = _base_config("show_brief")
        config["storage_records"]["briefs"] = [
            {"id": "b1", "organization_name": "Rendero Trust", "department": "investments",
             "headline": "Pipeline", "summary": "Active deals.", "priorities": ["Close Fund IV"],
             "risks": ["Timing"], "created_at": "2026-03-15", "updated_at": "2026-03-25"},
        ]
        result = agent.run_once(config, dry_run=True)
        assert result["status"] == "ok"
        assert result["normalized_request"]["mode"] == "brief"
