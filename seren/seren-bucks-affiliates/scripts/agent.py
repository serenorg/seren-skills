#!/usr/bin/env python3
"""Runtime stub for seren-bucks-affiliates."""

from __future__ import annotations

import argparse
import json

from bootstrap import bootstrap_affiliate_context, bootstrap_auth_and_db
from candidate_sync import sync_candidates
from common import load_config, utc_now
from digest import build_daily_digest
from drafting import build_draft_batches
from ranking import build_editable_top10
from reconcile import reconcile_signals
from sending import prepare_send_actions


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Seren Bucks affiliate skill stub.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to the runtime config file.",
    )
    parser.add_argument(
        "--command",
        default=None,
        choices=["bootstrap", "run", "status", "sync", "draft", "reconcile", "digest"],
        help="Optional command override. Defaults to config.inputs.command.",
    )
    return parser.parse_args()


def _bootstrap_only(config: dict) -> dict:
    auth_db = bootstrap_auth_and_db(config)
    if auth_db["status"] != "ok":
        return {
            "run_status": "blocked",
            "mode": "bootstrap",
            "generated_at": utc_now(),
            "error": auth_db,
        }

    affiliate = bootstrap_affiliate_context(config)
    if affiliate["status"] != "ok":
        return {
            "run_status": "blocked",
            "mode": "bootstrap",
            "generated_at": utc_now(),
            "auth_path": auth_db["auth_path"],
            "error": affiliate,
        }

    return {
        "run_status": "ok",
        "mode": "bootstrap",
        "generated_at": utc_now(),
        "auth_path": auth_db["auth_path"],
        "database_status": auth_db["database_status"],
        "program": affiliate["program"],
        "affiliate_feed_status": affiliate["affiliate_feed_status"],
    }


def _status(config: dict) -> dict:
    return {
        "run_status": "ok",
        "mode": "status",
        "generated_at": utc_now(),
        "program": config["program"],
        "database": config["database"],
        "limits": config["limits"],
        "approval": config["approval"],
        "dnc": config["dnc"],
        "daily_mode": "manual+digest",
    }


def _run_pipeline(config: dict, *, mode: str) -> dict:
    auth_db = bootstrap_auth_and_db(config)
    if auth_db["status"] != "ok":
        return {
            "run_status": "blocked",
            "mode": mode,
            "generated_at": utc_now(),
            "error": auth_db,
        }

    affiliate = bootstrap_affiliate_context(config)
    if affiliate["status"] != "ok":
        return {
            "run_status": "blocked",
            "mode": mode,
            "generated_at": utc_now(),
            "auth_path": auth_db["auth_path"],
            "error": affiliate,
        }

    sync_result = sync_candidates(config)
    proposal = build_editable_top10(sync_result["candidates"], config)
    drafts = build_draft_batches(proposal, config)
    send_plan = prepare_send_actions(drafts, config)
    reconciliation = reconcile_signals(config, sync_result, drafts)
    digest = build_daily_digest(
        config=config,
        auth_db=auth_db,
        affiliate=affiliate,
        sync_result=sync_result,
        proposal=proposal,
        drafts=drafts,
        send_plan=send_plan,
        reconciliation=reconciliation,
    )

    if mode == "sync":
        payload = sync_result
    elif mode == "draft":
        payload = {
            "proposal_top10": proposal,
            "drafts": drafts,
            "pending_approvals": send_plan,
        }
    elif mode == "reconcile":
        payload = reconciliation
    elif mode == "digest":
        payload = {"daily_digest": digest}
    else:
        payload = {
            "program": affiliate["program"],
            "affiliate_feed_status": affiliate["affiliate_feed_status"],
            "database_status": auth_db["database_status"],
            "provider_health": reconciliation["provider_health"],
            "candidate_sync": sync_result,
            "proposal_top10": proposal,
            "pending_approvals": send_plan,
            "dnc_events": reconciliation["dnc_events"],
            "daily_cap": digest["daily_cap"],
            "daily_digest": digest,
            "audit": reconciliation["audit"],
        }

    return {
        "skill": "seren-bucks-affiliates",
        "run_status": "ok",
        "mode": mode,
        "generated_at": utc_now(),
        "auth_path": auth_db["auth_path"],
        **payload,
    }


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    command = args.command or config["inputs"].get("command", "run")

    if command == "bootstrap":
        result = _bootstrap_only(config)
    elif command == "status":
        result = _status(config)
    else:
        result = _run_pipeline(config, mode=command)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
