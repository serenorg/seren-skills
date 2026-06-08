#!/usr/bin/env python3
"""Dry-run-first operator runtime for sfo-vs-mfo-operating-model-evaluator."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


SKILL_NAME = 'sfo-vs-mfo-operating-model-evaluator'
ISSUE_NUMBER = 942
PILLAR = 'complexity-management'
DEFAULT_DRY_RUN_TO = 'taariq@serendb.com'
PROJECT_NAME = 'family-office-sfo-vs-mfo-operating-model-evaluator'
DATABASE_NAME = 'family_office_sfo_vs_mfo_operating_model_evaluator'
STATE_TABLES = ['operating_cost_model', 'staffing_capability_matrix', 'benchmarked_mfo_offerings', 'technology_gap_log', 'succession_resilience_scores', 'recommendation_memos']
REQUIRED_OUTPUTS = ['cost_to_serve_model', 'service_level_gap_analysis', 'technology_gap_analysis', 'succession_scorecard', 'recommendation_memo', 'benchmarked_mfo_offerings']
HANDOFFS = ['complexity-management-router', 'external-advisor-management-plan']
AVAILABLE_CONNECTORS = ['docs', 'drive', 'exa', 'outlook', 'passwords', 'search', 'sheets', 'storage']
DEFAULT_PROFILE = {'title': 'SFO Vs MFO Operating Model Evaluator Operator', 'priority': 'P1', 'cadence': 'annual-operating-model-review-or-keyperson-event', 'gate': 'vendor contact, migration commitments, and staff changes require human approval', 'sample_subject': '$850M multi-entity family office operating model review', 'exception_flags': ['key_person_dependency', 'cost_gap_above_policy'], 'actions': ['model_current_sfo_costs', 'benchmark_mfo_offerings', 'score_service_and_succession_gaps', 'draft_operating_model_recommendation'], 'outputs': ['cost_to_serve_model', 'service_level_gap_analysis', 'technology_gap_analysis', 'succession_scorecard', 'recommendation_memo', 'benchmarked_mfo_offerings'], 'handoffs': ['complexity-management-router', 'external-advisor-management-plan'], 'always_review': True}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the family-office operator.")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--functional-test", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    return parser.parse_args()


def load_config(config_path: str) -> Dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def run_once(config: Dict[str, Any], *, allow_live: bool = False, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    dry_run = bool(config.get("dry_run", True))
    live_mode = bool(config.get("live_mode", False))
    if not dry_run:
        if not allow_live:
            raise RuntimeError("live run requires --allow-live")
        if not live_mode:
            raise RuntimeError("--allow-live also requires live_mode=true in config.json")

    inputs = _inputs(config)
    profile = _profile(config)
    event = _event(config, inputs, profile)
    exception_flags = [str(item) for item in event.get("exception_flags", []) if str(item)]
    mode = "dry-run" if dry_run else "live"
    request_key = f"{event['event_id']}:{mode}"
    review_required = bool(exception_flags) or bool(profile.get("always_review", True))
    approved = bool(inputs.get("approval_confirmed", False))
    executable = (not review_required) or (not dry_run and allow_live and live_mode and approved)
    status = "attention_required" if review_required and not executable else "clear"
    if dry_run and status == "clear":
        status = "dry_run_ready"

    approval_tasks = []
    if review_required:
        approval_tasks.append({
            "task_id": f"review::{event['event_id']}",
            "event_id": event["event_id"],
            "subject": event["subject"],
            "exception_flags": exception_flags,
            "gate": profile["gate"],
            "status": "open" if not executable else "approved",
        })

    return {
        "skill": SKILL_NAME,
        "issue": ISSUE_NUMBER,
        "status": status,
        "dry_run": dry_run,
        "live_mode": live_mode,
        "as_of": today.isoformat(),
        "dry_run_to": _dry_run_to(config, inputs),
        "connectors": AVAILABLE_CONNECTORS,
        "schema_guard": _schema_guard(),
        "secrets_plan": _secrets_plan(config),
        "operator_profile": {
            "title": profile["title"],
            "priority": profile["priority"],
            "cadence": profile["cadence"],
            "gate": profile["gate"],
        },
        "event": {
            "event_id": event["event_id"],
            "subject": event["subject"],
            "source": event.get("source"),
            "amount_usd": event.get("amount_usd"),
            "exception_flags": exception_flags,
        },
        "source_artifacts": _source_artifacts(config, event),
        "approval_required": review_required,
        "executable": executable,
        "approval_tasks": approval_tasks,
        "audit_events": [{
            "request_key": request_key,
            "event_id": event["event_id"],
            "mode": mode,
            "status": status,
            "exception_flags": exception_flags,
        }],
        "output_bundle": _output_bundle(profile, event, dry_run),
        "planned_actions": _planned_actions(dry_run, profile, exception_flags),
        "handoffs": [{"skill": handoff, "status": "staged_for_review"} for handoff in profile["handoffs"]],
        "external_actions": [] if dry_run else [action for action in profile["actions"] if executable],
    }


def run_functional_test(config: Dict[str, Any]) -> Dict[str, Any]:
    profile = _profile(config)
    dry_run_to = _dry_run_to(config, _inputs(config))
    sample = {
        "dry_run": True,
        "live_mode": False,
        "dry_run_to": dry_run_to,
        "operator_profile": profile,
        "inputs": {
            "event_id": "functional-event-001",
            "subject": profile["sample_subject"],
            "approval_confirmed": False,
        },
        "control_event": {
            "event_id": "functional-event-001",
            "subject": profile["sample_subject"],
            "source": "synthetic-dry-run",
            "amount_usd": 1000,
            "exception_flags": profile["exception_flags"],
        },
    }
    result = run_once(sample, allow_live=False, today=date(2026, 6, 8))
    delivered = set(result["output_bundle"]["deliverables"])
    checks = {
        "schema_guard": result["schema_guard"]["project"] == PROJECT_NAME and bool(result["schema_guard"]["tables"]),
        "passwords_secret_plan": result["secrets_plan"]["provider"] == "seren-passwords",
        "idempotency_key": result["audit_events"][0]["request_key"] == "functional-event-001:dry-run",
        "approval_gate": result["approval_required"] is True and result["executable"] is False,
        "dry_run_digest": result["dry_run_to"] == dry_run_to,
        "required_outputs": set(REQUIRED_OUTPUTS).issubset(delivered),
        "no_live_side_effects": result["external_actions"] == [],
        "handoffs_staged": len(result["handoffs"]) == len(profile["handoffs"]),
    }
    return {
        "skill": SKILL_NAME,
        "issue": ISSUE_NUMBER,
        "status": "all_green" if all(checks.values()) else "failed",
        "dry_run": True,
        "dry_run_to": dry_run_to,
        "checked_features": [name for name, ok in checks.items() if ok],
        "failed_features": [name for name, ok in checks.items() if not ok],
        "sample_result": result,
    }


def _inputs(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("inputs", {})
    return raw if isinstance(raw, dict) else {}


def _profile(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("operator_profile", {})
    profile = DEFAULT_PROFILE.copy()
    if isinstance(raw, dict):
        profile.update(raw)
    profile["exception_flags"] = [str(item) for item in profile.get("exception_flags", [])]
    profile["actions"] = [str(item) for item in profile.get("actions", REQUIRED_OUTPUTS)]
    profile["outputs"] = [str(item) for item in profile.get("outputs", REQUIRED_OUTPUTS)]
    profile["handoffs"] = [str(item) for item in profile.get("handoffs", HANDOFFS)]
    return profile


def _event(config: Dict[str, Any], inputs: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("control_event", {})
    event = raw.copy() if isinstance(raw, dict) else {}
    event["event_id"] = str(event.get("event_id") or inputs.get("event_id") or "unassigned-event")
    event["subject"] = str(event.get("subject") or inputs.get("subject") or profile["sample_subject"])
    event.setdefault("exception_flags", profile["exception_flags"])
    event.setdefault("source", "synthetic-dry-run")
    return event


def _dry_run_to(config: Dict[str, Any], inputs: Dict[str, Any]) -> str:
    return str(config.get("dry_run_to") or inputs.get("dry_run_to") or DEFAULT_DRY_RUN_TO)


def _schema_guard() -> Dict[str, Any]:
    return {
        "project": PROJECT_NAME,
        "database": DATABASE_NAME,
        "tables": STATE_TABLES,
        "runs_before_reads_or_writes": True,
        "create_if_missing": True,
    }


def _secrets_plan(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("secrets", {})
    secrets = raw if isinstance(raw, dict) else {}
    return {
        "provider": "seren-passwords",
        "env_first": True,
        "vault_name": str(secrets.get("vault_name", "Family Office Operations")),
        "forbid_hardcoded_vault_ids": True,
        "forbid_glide_vault": True,
        "items": sorted(str(item) for item in secrets.get("items", [])),
    }


def _source_artifacts(config: Dict[str, Any], event: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = config.get("source_artifacts")
    if isinstance(raw, list) and raw:
        return [item for item in raw if isinstance(item, dict)]
    return [{
        "artifact_id": f"synthetic::{event['event_id']}",
        "kind": "synthetic-dry-run",
        "subject": event["subject"],
        "citation_status": "placeholder-ready",
    }]


def _output_bundle(profile: Dict[str, Any], event: Dict[str, Any], dry_run: bool) -> Dict[str, Any]:
    deliverables = [str(item) for item in profile["outputs"]]
    return {
        "status": "drafted",
        "dry_run": dry_run,
        "subject": event["subject"],
        "deliverables": deliverables,
        "source_citations_required": True,
        "human_review_required": True,
        "items": [
            {
                "name": deliverable,
                "status": "ready_for_review",
                "source": "synthetic-dry-run",
            }
            for deliverable in deliverables
        ],
    }


def _planned_actions(dry_run: bool, profile: Dict[str, Any], exception_flags: List[str]) -> List[Dict[str, Any]]:
    mode = "dry-run" if dry_run else "live"
    return [
        {"action": action, "mode": mode, "approval_required": bool(exception_flags)}
        for action in profile["actions"]
    ]


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if args.functional_test:
        result = run_functional_test(config)
    else:
        result = run_once(config, allow_live=args.allow_live)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
