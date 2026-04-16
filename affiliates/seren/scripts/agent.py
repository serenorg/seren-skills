#!/usr/bin/env python3
"""Runtime stub for seren-affiliate.

The live runtime harness executes the workflow DAG declared in skill.spec.yaml
against Seren publisher connectors. This module is the reference and smoke
test implementation: each workflow step is represented as a pure Python
function that returns a structured dict. It is safe to invoke offline.
"""

from __future__ import annotations

import argparse
import json

from block import block_email
from bootstrap import bootstrap_auth_and_db, sync_affiliate_profile
from common import (
    DEFAULT_CONFIG,
    daily_cap_from_input,
    load_config,
    new_run_id,
    require_approve_draft_json_pairing,
    utc_now,
)
from draft import await_approval, draft_pitch
from ingest import enforce_daily_cap, filter_eligible, ingest_contacts, resolve_provider
from send import merge_and_send
from status import fetch_live_stats, render_report
from sync import select_program, sync_joined_programs, sync_remote_unsubscribes

COMMAND_CHOICES = (
    "bootstrap",
    "run",
    "status",
    "sync",
    "ingest",
    "draft",
    "send",
    "block",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Seren Affiliate skill stub.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to the runtime config file.",
    )
    parser.add_argument(
        "--command",
        default=None,
        choices=COMMAND_CHOICES,
        help="Optional command override. Defaults to config.inputs.command.",
    )
    return parser.parse_args()


def _bootstrap_stage(config: dict) -> dict:
    auth_db = bootstrap_auth_and_db(config)
    if auth_db["status"] != "ok":
        return {"stage_status": "blocked", "auth_db": auth_db}
    profile_result = sync_affiliate_profile(config)
    if profile_result["status"] != "ok":
        return {
            "stage_status": "blocked",
            "auth_db": auth_db,
            "profile": profile_result,
        }
    programs_result = sync_joined_programs(config)
    if programs_result["status"] != "ok":
        return {
            "stage_status": "blocked",
            "auth_db": auth_db,
            "profile": profile_result,
            "programs": programs_result,
        }
    return {
        "stage_status": "ok",
        "auth_db": auth_db,
        "profile": profile_result,
        "programs": programs_result,
    }


def _bootstrap_only(config: dict, run_id: str) -> dict:
    stage = _bootstrap_stage(config)
    return {
        "skill": "seren-affiliate",
        "run_id": run_id,
        "command": "bootstrap",
        "run_status": "ok" if stage["stage_status"] == "ok" else "blocked",
        "generated_at": utc_now(),
        **{k: v for k, v in stage.items() if k != "stage_status"},
    }


def _status(config: dict, run_id: str) -> dict:
    stage = _bootstrap_stage(config)
    if stage["stage_status"] != "ok":
        return {
            "skill": "seren-affiliate",
            "run_id": run_id,
            "command": "status",
            "run_status": "blocked",
            "generated_at": utc_now(),
            **{k: v for k, v in stage.items() if k != "stage_status"},
        }
    program_slug = str(config["inputs"].get("program_slug", "")).strip()
    live = fetch_live_stats(config=config, program_slug=program_slug) if program_slug else None
    return {
        "skill": "seren-affiliate",
        "run_id": run_id,
        "command": "status",
        "run_status": "ok",
        "generated_at": utc_now(),
        "auth_db": stage["auth_db"],
        "profile": stage["profile"]["profile"],
        "programs": stage["programs"]["programs"],
        "live": live,
    }


def _block(config: dict, run_id: str) -> dict:
    stage = _bootstrap_stage(config)
    if stage["stage_status"] != "ok":
        return {
            "skill": "seren-affiliate",
            "run_id": run_id,
            "command": "block",
            "run_status": "blocked",
            "generated_at": utc_now(),
            **{k: v for k, v in stage.items() if k != "stage_status"},
        }
    result = block_email(config)
    persist_unsubscribes: list[dict] = []
    if result["status"] == "ok":
        persist_unsubscribes.append(
            {
                "email": result["unsubscribe"]["email"],
                "unsubscribed_at": result["unsubscribe"]["unsubscribed_at"],
                "source": "operator_manual",
                "agent_id": stage["profile"]["profile"]["agent_id"],
            }
        )
    return {
        "skill": "seren-affiliate",
        "run_id": run_id,
        "command": "block",
        "run_status": "ok" if result["status"] == "ok" else "blocked",
        "generated_at": utc_now(),
        "block_result": result,
        "persist": {"unsubscribes": persist_unsubscribes},
    }


def _seed_watermark(config: dict, agent_id: str) -> str | None:
    """Reference implementation: in production the runtime harness reads
    sync_state.last_synced_at WHERE agent_id=? AND source='link_click'.
    Here we let the operator or a test fixture seed it via simulate."""
    simulate = config.get("simulate", {})
    seeded = simulate.get("sync_state_watermark")
    if isinstance(seeded, dict):
        return seeded.get(agent_id)
    if isinstance(seeded, str):
        return seeded
    return None


def _seed_persisted_blocklist(config: dict) -> set[str]:
    """Reference implementation: the runtime harness fills this via
    SELECT email FROM unsubscribes. Tests can seed it with
    simulate.unsubscribed_emails so the end-to-end filter path is verifiable."""
    simulate = config.get("simulate", {})
    seeded = simulate.get("unsubscribed_emails") or []
    return {str(e).strip().lower() for e in seeded if str(e).strip()}


def _token_resolver(config: dict):
    """Reference implementation: in production the runtime harness resolves
    tokens via SELECT contact_email FROM distributions WHERE unsubscribe_token=?.
    Here we let a test fixture provide a token->email map."""
    simulate = config.get("simulate", {})
    token_map = simulate.get("distributions_by_token") or {}

    def _resolve(token: str) -> str | None:
        return token_map.get(token)

    return _resolve


def _hard_bounce_rows(send_result: dict | None, agent_id: str) -> list[dict]:
    if not send_result:
        return []
    out: list[dict] = []
    for row in send_result.get("new_unsubscribes", []) or []:
        if row.get("source") != "hard_bounce":
            continue
        out.append(
            {
                "email": row["email"],
                "unsubscribed_at": row["unsubscribed_at"],
                "source": "hard_bounce",
                "agent_id": agent_id,
                "unsubscribe_token": row.get("unsubscribe_token"),
            }
        )
    return out


def _run_pipeline(config: dict, *, command: str, run_id: str) -> dict:
    pairing_error = require_approve_draft_json_pairing(config)
    if pairing_error is not None:
        return {
            "skill": "seren-affiliate",
            "run_id": run_id,
            "command": command,
            "run_status": "blocked",
            "generated_at": utc_now(),
            "error": pairing_error,
        }

    stage = _bootstrap_stage(config)
    if stage["stage_status"] != "ok":
        return {
            "skill": "seren-affiliate",
            "run_id": run_id,
            "command": command,
            "run_status": "blocked",
            "generated_at": utc_now(),
            **{k: v for k, v in stage.items() if k != "stage_status"},
        }

    programs = stage["programs"]["programs"]
    program_selection = select_program(config, programs)
    if program_selection["status"] != "ok":
        return {
            "skill": "seren-affiliate",
            "run_id": run_id,
            "command": command,
            "run_status": "blocked",
            "generated_at": utc_now(),
            "profile": stage["profile"],
            "programs": stage["programs"],
            "program_selection": program_selection,
        }
    program = program_selection["program"]

    provider = resolve_provider(config)
    if provider["status"] != "ok":
        return {
            "skill": "seren-affiliate",
            "run_id": run_id,
            "command": command,
            "run_status": "blocked",
            "generated_at": utc_now(),
            "provider": provider,
        }

    ingest = ingest_contacts(config)
    if ingest["status"] != "ok":
        return {
            "skill": "seren-affiliate",
            "run_id": run_id,
            "command": command,
            "run_status": "blocked",
            "generated_at": utc_now(),
            "ingest": ingest,
        }

    remote_sync = sync_remote_unsubscribes(
        config=config,
        agent_id=stage["profile"]["profile"]["agent_id"],
        watermark=_seed_watermark(config, stage["profile"]["profile"]["agent_id"]),
        resolve_token=_token_resolver(config),
    )
    persisted_blocklist = _seed_persisted_blocklist(config)
    remote_blocklist = {row["email"] for row in remote_sync["new_unsubscribes"]}
    unsubscribes_set = persisted_blocklist | remote_blocklist

    eligibility = filter_eligible(
        contacts=ingest["contacts"],
        program_slug=program["program_slug"],
        already_sent_for_program=set(),
        unsubscribes=unsubscribes_set,
    )
    cap = daily_cap_from_input(config)
    cap_summary = enforce_daily_cap(
        eligible=eligibility["eligible"],
        cap=cap,
        already_sent_today=0,
    )

    if command == "ingest":
        return {
            "skill": "seren-affiliate",
            "run_id": run_id,
            "command": command,
            "run_status": "ok",
            "generated_at": utc_now(),
            "profile": stage["profile"]["profile"],
            "program": program,
            "ingest": ingest,
            "eligibility": eligibility,
            "cap_summary": cap_summary,
        }

    draft_result = draft_pitch(config=config, program=program, run_id=run_id)
    if draft_result["status"] != "ok":
        return {
            "skill": "seren-affiliate",
            "run_id": run_id,
            "command": command,
            "run_status": "blocked",
            "generated_at": utc_now(),
            "draft": draft_result,
        }
    draft = draft_result["draft"]

    if command == "draft":
        return {
            "skill": "seren-affiliate",
            "run_id": run_id,
            "command": command,
            "run_status": "ok",
            "generated_at": utc_now(),
            "profile": stage["profile"]["profile"],
            "program": program,
            "draft": draft,
            "eligibility": eligibility,
            "cap_summary": cap_summary,
            "next_step": "Re-run `send` with approve_draft=true + json_output=true to dispatch.",
        }

    approval = await_approval(config=config, draft=draft)
    send_result: dict | None = None
    if approval["status"] == "approved":
        send_result = merge_and_send(
            config=config,
            run_id=run_id,
            profile=stage["profile"]["profile"],
            program=program,
            provider_used=provider["provider_used"],
            draft=draft,
            sendable=cap_summary["sendable"],
            approval=approval,
        )

    live = fetch_live_stats(config=config, program_slug=program["program_slug"])
    report = render_report(
        config=config,
        run_id=run_id,
        command=command,
        program=program,
        profile=stage["profile"]["profile"],
        provider_used=provider["provider_used"],
        ingest_summary=ingest,
        eligibility=eligibility,
        cap_summary=cap_summary,
        draft=draft,
        approval=approval,
        send_result=send_result,
        live=live,
    )

    persist_unsubscribes: list[dict] = list(remote_sync["new_unsubscribes"])
    persist_unsubscribes.extend(_hard_bounce_rows(send_result, stage["profile"]["profile"]["agent_id"]))
    persist_sync_state: list[dict] = []
    if not remote_sync["stale"]:
        persist_sync_state.append(
            {
                "agent_id": stage["profile"]["profile"]["agent_id"],
                "source": "link_click",
                "last_synced_at": remote_sync["next_watermark"],
            }
        )

    return {
        "skill": "seren-affiliate",
        "run_id": run_id,
        "command": command,
        "run_status": "ok",
        "generated_at": utc_now(),
        "profile": stage["profile"]["profile"],
        "program": program,
        "provider": provider,
        "ingest": ingest,
        "remote_sync": remote_sync,
        "eligibility": eligibility,
        "cap_summary": cap_summary,
        "draft": draft,
        "approval": approval,
        "send": send_result,
        "live": live,
        "report": report,
        "persist": {
            "unsubscribes": persist_unsubscribes,
            "sync_state": persist_sync_state,
        },
    }


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    command = args.command or config["inputs"].get("command", DEFAULT_CONFIG["inputs"]["command"])
    run_id = new_run_id(prefix=command)

    if command == "bootstrap":
        result = _bootstrap_only(config, run_id)
    elif command == "status":
        result = _status(config, run_id)
    elif command == "block":
        result = _block(config, run_id)
    elif command == "sync":
        result = _bootstrap_only(config, run_id)
        result["command"] = "sync"
    else:
        result = _run_pipeline(config, command=command, run_id=run_id)

    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
