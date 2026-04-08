#!/usr/bin/env python3
"""Manage seren-cron local pull schedules for kalshi-bot."""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from seren_client import SerenClient

SKILL_SLUG = "kalshi-bot"
DEFAULT_JOB_NAME = "kalshi-bot-local-pull"
DEFAULT_CRON_EXPRESSION = "0 */2 * * *"
DEFAULT_POLL_INTERVAL_SECONDS = 30


def current_machine_label() -> str:
    """Return a friendly machine label for runner registration."""
    hostname = socket.gethostname().split('.')[0]
    return f"{hostname}-{platform.system().lower()}"


def default_runner_name(machine_label: str = '') -> str:
    """Generate a default runner name."""
    label = machine_label or current_machine_label()
    return f"{SKILL_SLUG}-{label}"


def _call_publisher(
    client: SerenClient,
    publisher: str,
    method: str,
    path: str,
    body: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Call a seren publisher and return the response."""
    return client.call_publisher(
        publisher=publisher,
        method=method,
        path=path,
        body=body or {},
    )


def _list_jobs(client: SerenClient) -> list:
    """List all seren-cron jobs and filter to this skill."""
    response = _call_publisher(client, 'seren-cron', 'GET', '/api/v1/jobs')
    jobs = response.get('jobs', response.get('data', []))
    if not isinstance(jobs, list):
        jobs = []
    return [
        j for j in jobs
        if _is_skill_job(j)
    ]


def _list_runners(client: SerenClient) -> list:
    """List all seren-cron runners and filter to this skill."""
    response = _call_publisher(client, 'seren-cron', 'GET', '/api/v1/runners')
    runners = response.get('runners', response.get('data', []))
    if not isinstance(runners, list):
        runners = []
    return [
        r for r in runners
        if _is_skill_runner(r)
    ]


def _is_skill_job(job: Dict[str, Any]) -> bool:
    """Check if a job belongs to this skill."""
    payload = job.get('local_payload', {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    return (
        str(job.get('execution_mode', '')) == 'local_pull'
        and str(payload.get('skill_slug', '')) == SKILL_SLUG
    )


def _is_skill_runner(runner: Dict[str, Any]) -> bool:
    """Check if a runner belongs to this skill."""
    slug = str(runner.get('skill_slug', ''))
    if slug:
        return slug == SKILL_SLUG
    return str(runner.get('name', '')).startswith(f"{SKILL_SLUG}-")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage seren-cron local pull schedules for kalshi-bot."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create or update the local pull runner and job.")
    create.add_argument("--name", default=DEFAULT_JOB_NAME, help="seren-cron job name.")
    create.add_argument("--schedule", default=DEFAULT_CRON_EXPRESSION, help="Cron expression.")
    create.add_argument("--timezone", default="UTC", help="IANA timezone name.")
    create.add_argument("--config", default="config.json", help="Config path passed to agent.py.")
    create.add_argument(
        "--runner-name", default="",
        help="Optional runner name. Defaults to skill slug + hostname.",
    )
    create.add_argument(
        "--machine-label", default=current_machine_label(),
        help="Friendly machine label stored with the runner.",
    )
    create.add_argument(
        "--poll-interval-seconds", type=int, default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Runner poll cadence.",
    )
    dry_mode = create.add_mutually_exclusive_group()
    dry_mode.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Schedule paper trading only.",
    )
    dry_mode.add_argument(
        "--live", dest="dry_run", action="store_false",
        help="Schedule live trading.",
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
        client = SerenClient()
    except ValueError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 1

    try:
        if args.command == "create":
            runner_name = args.runner_name.strip() or default_runner_name(args.machine_label)

            # Register or find runner
            runner_body = {
                'name': runner_name,
                'skill_slug': SKILL_SLUG,
                'machine_label': args.machine_label,
                'poll_interval_seconds': args.poll_interval_seconds,
            }
            runner = _call_publisher(client, 'seren-cron', 'POST', '/api/v1/runners', runner_body)

            runner_id = runner.get('id', runner.get('runner_id', ''))

            # Create job
            job_body = {
                'name': args.name,
                'cron_expression': args.schedule,
                'timezone': args.timezone,
                'execution_mode': 'local_pull',
                'runner_id': runner_id,
                'local_payload': json.dumps({
                    'skill_slug': SKILL_SLUG,
                    'config_path': args.config,
                    'dry_run': bool(args.dry_run),
                }),
            }
            result = _call_publisher(client, 'seren-cron', 'POST', '/api/v1/jobs', job_body)
            result['runner_id'] = runner_id
            result['runner_name'] = runner_name

        elif args.command == "list":
            result = {"jobs": _list_jobs(client)}

        elif args.command == "list-runners":
            result = {"runners": _list_runners(client)}

        elif args.command == "pause":
            result = _call_publisher(
                client, 'seren-cron', 'POST',
                f'/api/v1/jobs/{args.job_id}/pause',
            )

        elif args.command == "resume":
            result = _call_publisher(
                client, 'seren-cron', 'POST',
                f'/api/v1/jobs/{args.job_id}/resume',
            )

        elif args.command == "delete":
            result = _call_publisher(
                client, 'seren-cron', 'DELETE',
                f'/api/v1/jobs/{args.job_id}',
            )

        elif args.command == "delete-runner":
            result = _call_publisher(
                client, 'seren-cron', 'DELETE',
                f'/api/v1/runners/{args.runner_id}',
            )
        else:
            result = {"error": f"Unknown command: {args.command}"}

    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 1

    print(json.dumps({"status": "ok", "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
