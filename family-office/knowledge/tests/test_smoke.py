from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATH = SCRIPT_DIR / "agent.py"


def _load_agent_module():
    spec = importlib.util.spec_from_file_location("knowledge_agent", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
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
        "current_date": "2026-03-27",
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
        },
        "sharepoint_context": [],
        "asana_context": [],
        "documents": [],
        "interview_transcript": [],
    }


def test_capture_workflow_archives_transcript_and_persists_entries(monkeypatch) -> None:
    agent = _load_agent_module()
    monkeypatch.setattr(
        agent,
        "fire_reward_webhook",
        lambda **_: {"status": "sent", "transaction_id": "tx-capture"},
    )

    config = _base_config("capture")
    config["sharepoint_context"] = [
        {"title": "Tax memo", "summary": "Use QSBS where available.", "department": "investments"}
    ]
    config["documents"] = [
        {"title": "2026 planning doc", "text": "Tax strategy should prioritize entity cleanup."}
    ]
    config["interview_transcript"] = [
        {"speaker": "user", "text": "We should centralize tax planning before Q3."},
        {"speaker": "assistant", "text": "Who owns the workstream?"},
        {"speaker": "user", "text": "Finance ops owns it with legal support."},
    ]

    result = agent.run_once(config, dry_run=True)

    assert result["status"] == "ok"
    assert result["normalized_request"]["mode"] == "capture"
    assert result["transcript"]["turn_count"] == 3
    assert len(result["knowledge_entries"]) >= 2
    assert len(result["storage_state"]["transcripts"]) == 1
    assert len(result["storage_state"]["knowledge_entries"]) == len(result["knowledge_entries"])
    assert result["reward"]["status"] == "sent"
    assert result["storage_state"]["reward_audits"][0]["transaction_id"] == "tx-capture"


def test_retrieve_workflow_applies_access_and_freshness_and_logs_event(monkeypatch) -> None:
    agent = _load_agent_module()
    monkeypatch.setattr(
        agent,
        "fire_reward_webhook",
        lambda **_: {"status": "sent", "transaction_id": "tx-retrieve"},
    )

    config = _base_config("recall")
    config["storage_records"]["knowledge_entries"] = [
        {
            "id": "entry-fresh",
            "organization_name": "Rendero Trust",
            "department": "investments",
            "topic": "tax strategy",
            "title": "QSBS planning",
            "summary": "Evaluate QSBS eligibility before any restructure.",
            "content": "Evaluate QSBS eligibility before any restructure.",
            "owner_id": "user-999",
            "access_scope": "public",
            "created_at": "2026-03-20",
            "updated_at": "2026-03-20",
            "stale_after_days": 30,
        },
        {
            "id": "entry-private",
            "organization_name": "Rendero Trust",
            "department": "investments",
            "topic": "tax strategy",
            "title": "Private note",
            "summary": "This should not be visible.",
            "content": "This should not be visible.",
            "owner_id": "other-user",
            "access_scope": "private",
            "created_at": "2026-03-20",
            "updated_at": "2026-03-20",
            "stale_after_days": 30,
        },
        {
            "id": "entry-stale",
            "organization_name": "Rendero Trust",
            "department": "investments",
            "topic": "tax strategy",
            "title": "Old planning note",
            "summary": "Legacy tax strategy assumptions from last year.",
            "content": "Legacy tax strategy assumptions from last year.",
            "owner_id": "user-123",
            "access_scope": "team",
            "created_at": "2025-12-01",
            "updated_at": "2025-12-01",
            "stale_after_days": 30,
        },
    ]

    result = agent.run_once(config, dry_run=True)
    result_ids = [entry["id"] for entry in result["retrieval_results"]["records"]]
    freshness = {entry["id"]: entry["freshness"] for entry in result["retrieval_results"]["records"]}

    assert result["normalized_request"]["mode"] == "retrieve"
    assert result_ids == ["entry-fresh", "entry-stale"]
    assert freshness["entry-fresh"] == "fresh"
    assert freshness["entry-stale"] == "stale"
    assert len(result["storage_state"]["retrieval_events"]) == 1
    assert result["storage_state"]["retrieval_events"][0]["result_ids"] == result_ids
    assert result["reward"]["event_type"] == "knowledge_retrieval"


def test_brief_workflow_renders_current_working_brief_with_synced_context() -> None:
    agent = _load_agent_module()

    config = _base_config("show_brief")
    config["inputs"]["query_text"] = "investor pipeline"
    config["storage_records"]["briefs"] = [
        {
            "id": "brief-current",
            "organization_name": "Rendero Trust",
            "department": "investments",
            "headline": "Investor pipeline",
            "summary": "Top priority is converting three warm LP conversations.",
            "priorities": ["Close Fund IV anchor", "Refresh monthly update"],
            "risks": ["Data room is stale"],
            "created_at": "2026-03-15",
            "updated_at": "2026-03-25",
        }
    ]
    config["sharepoint_context"] = [
        {"title": "Pipeline memo", "summary": "Warm LPs want fee clarity.", "department": "investments"}
    ]
    config["asana_context"] = [
        {"title": "Prepare LP update", "summary": "Draft April investor letter.", "department": "investments"}
    ]
    config["documents"] = [
        {"title": "Data room checklist", "text": "Refresh portfolio metrics before the next LP call."}
    ]

    result = agent.run_once(config, dry_run=True)

    assert result["normalized_request"]["mode"] == "brief"
    assert result["working_brief"]["headline"] == "Investor pipeline"
    assert result["working_brief"]["source_counts"]["sharepoint"] == 1
    assert result["working_brief"]["source_counts"]["asana"] == 1
    assert "SharePoint: Pipeline memo" in result["working_brief"]["notes"]
    assert result["reward"]["status"] == "skipped"


def test_diff_workflow_compares_current_brief_against_first_brief() -> None:
    agent = _load_agent_module()

    config = _base_config("compare")
    config["inputs"]["query_text"] = "investor pipeline"
    config["storage_records"]["briefs"] = [
        {
            "id": "brief-first",
            "organization_name": "Rendero Trust",
            "department": "investments",
            "headline": "Investor pipeline",
            "summary": "Initial LP pipeline is exploratory.",
            "priorities": ["Draft initial LP list"],
            "risks": ["No anchor investor"],
            "created_at": "2026-01-10",
            "updated_at": "2026-01-10",
        },
        {
            "id": "brief-current",
            "organization_name": "Rendero Trust",
            "department": "investments",
            "headline": "Investor pipeline",
            "summary": "Top priority is converting three warm LP conversations.",
            "priorities": ["Draft initial LP list", "Close Fund IV anchor"],
            "risks": ["Data room is stale"],
            "created_at": "2026-03-15",
            "updated_at": "2026-03-25",
        },
    ]

    result = agent.run_once(config, dry_run=True)
    changes = result["working_brief"]["comparison"]["changes"]

    assert result["normalized_request"]["mode"] == "diff"
    assert any(change["field"] == "summary" for change in changes)
    assert {"field": "priorities", "change": "added", "value": "Close Fund IV anchor"} in changes
