#!/usr/bin/env python3
"""Manage seren-cron local pull schedules for polymarket-bot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from polymarket_live import (  # noqa: E402
    DEFAULT_SEREN_CRON_POLL_INTERVAL_SECONDS,
    call_publisher_json,
    current_machine_label,
    default_local_pull_runner_name,
    list_seren_cron_jobs,
    list_seren_cron_runners,
    safe_str,
    setup_local_pull_schedule,
)


SKILL_SLUG = "polymarket-bot"
DEFAULT_JOB_NAME = "polymarket-bot-local-pull"
DEFAULT_CRON_EXPRESSION = "0 */2 * * *"
DEFAULT_RUN_TYPE = "scan"


def _job_payload(job: dict[str, Any]) -> dict[str, Any]:
    payload = job.get("local_payload")
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _is_skill_job(job: dict[str, Any]) -> bool:
    payload = _job_payload(job)
    return (
        safe_str(job.get("execution_mode"), "") == "local_pull"
        and safe_str(payload.get("skill_slug"), "") == SKILL_SLUG
    )


def _is_skill_runner(runner: dict[str, Any]) -> bool:
    skill_slug = safe_str(runner.get("skill_slug"), "")
    if skill_slug:
        return skill_slug == SKILL_SLUG
    return safe_str(runner.get("name"), "").startswith(f"{SKILL_SLUG}-")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage seren-cron local pull schedules for polymarket-bot."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create or update the local pull runner and job.")
    create.add_argument("--name", default=DEFAULT_JOB_NAME, help="seren-cron job name.")
    create.add_argument("--schedule", default=DEFAULT_CRON_EXPRESSION, help="Cron expression.")
    create.add_argument("--timezone", default="UTC", help="IANA timezone name.")
    create.add_argument("--config", default="config.json", help="Config path passed to scripts/agent.py.")
    create.add_argument(
        "--run-type",
        choices=("scan", "monitor"),
        default=DEFAULT_RUN_TYPE,
        help="Schedule a full scan or a monitor-only pass.",
    )
    create.add_argument(
        "--runner-name",
        default="",
        help="Optional runner name. Defaults to skill slug + hostname.",
    )
    create.add_argument(
        "--machine-label",
        default=current_machine_label(),
        help="Friendly machine label stored with the runner.",
    )
    create.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=DEFAULT_SEREN_CRON_POLL_INTERVAL_SECONDS,
        help="Runner poll cadence.",
    )
    dry_mode = create.add_mutually_exclusive_group()
    dry_mode.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Schedule paper trading only.",
    )
    dry_mode.add_argument(
        "--live",
        dest="dry_run",
        action="store_false",
        help="Schedule live trading. Review credentials and budget first.",
    )
    create.set_defaults(dry_run=True)

    sub.add_parser("list", help="List local pull jobs for this skill.")
    sub.add_parser("list-runners", help="List runners for this skill.")

    pause = sub.add_parser("pause", help="Pause a job.")
    pause.add_argument("--job-id", required=True)

    resume = sub.add_parser("resume", help="Resume a job.")
    resume.add_argument("--job-id", required=True)

    delete = sub.add_parser("delete", help="Delete a job.")
    delete.add_argument("--job-id", required=True)

    delete_runner = sub.add_parser("delete-runner", help="Delete a runner.")
    delete_runner.add_argument("--runner-id", required=True)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "create":
            runner_name = args.runner_name.strip() or default_local_pull_runner_name(
                SKILL_SLUG,
                args.machine_label,
            )
            result = setup_local_pull_schedule(
                skill_slug=SKILL_SLUG,
                runner_name=runner_name,
                machine_label=args.machine_label,
                poll_interval_seconds=args.poll_interval_seconds,
                job_name=args.name,
                cron_expression=args.schedule,
                timezone_name=args.timezone,
                config_path=args.config,
                run_type=args.run_type,
                local_payload={"dry_run": bool(args.dry_run), "run_type": args.run_type},
                timeout_seconds=30.0,
            )
        elif args.command == "list":
            result = {"jobs": [job for job in list_seren_cron_jobs(timeout_seconds=30.0) if _is_skill_job(job)]}
        elif args.command == "list-runners":
            result = {
                "runners": [runner for runner in list_seren_cron_runners(timeout_seconds=30.0) if _is_skill_runner(runner)]
            }
        elif args.command == "pause":
            result = call_publisher_json(
                publisher="seren-cron",
                method="POST",
                path=f"/api/jobs/{args.job_id}/pause",
                body={},
                timeout_seconds=30.0,
            )
        elif args.command == "resume":
            result = call_publisher_json(
                publisher="seren-cron",
                method="POST",
                path=f"/api/jobs/{args.job_id}/resume",
                body={},
                timeout_seconds=30.0,
            )
        elif args.command == "delete":
            result = call_publisher_json(
                publisher="seren-cron",
                method="DELETE",
                path=f"/api/jobs/{args.job_id}",
                timeout_seconds=30.0,
            )
        else:
            result = call_publisher_json(
                publisher="seren-cron",
                method="DELETE",
                path=f"/api/runners/{args.runner_id}",
                timeout_seconds=30.0,
            )
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 1

    print(json.dumps({"status": "ok", "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
