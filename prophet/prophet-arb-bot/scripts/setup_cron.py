#!/usr/bin/env python3
"""Manage the prophet-arb-bot seren-cron schedule.

The user runs `setup_cron.py create` once after their first manual
`setup` succeeds; the resulting job fires `agent.py --command run --yes-live`
every hour via the local-pull runner. (--yes-live is opt-in; ticks scheduled
without --yes-live emit dry-run intents only.)

Subcommands:
  create          register the runner + upsert the local-pull job
  list            list jobs owned by this skill
  list-runners    list runners owned by this skill
  pause           pause a job
  resume          resume a paused job
  delete          delete a job
  delete-runner   delete a runner
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from seren_cron_client import (  # noqa: E402
    DEFAULT_CRON_EXPRESSION,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_TIMEZONE_NAME,
    SKILL_SLUG,
    HttpGateway,
    SerenCronClient,
    current_machine_label,
    current_platform_label,
    default_job_name,
    default_runner_name,
    default_user_id_short,
)


def build_local_payload(
    *,
    config_path: str,
    prophet_email: str,
    email_provider: str,
    bounty_id: str | None = None,
    yes_live: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Compose the per-tick payload the runner injects into agent.py.

    Every tick runs `agent.py --command run --json-output` with the
    user's saved inputs. The `yes_live` flag controls whether the run
    submits real Prophet orders or just emits dry-run intents.
    """
    payload: dict[str, Any] = {
        "skill_slug": SKILL_SLUG,
        "command": "run",
        "config_path": config_path,
        "prophet_email": prophet_email,
        "email_provider": email_provider,
        "json_output": True,
        "yes_live": bool(yes_live) and not bool(dry_run),
        "dry_run": bool(dry_run),
    }
    if bounty_id:
        payload["bounty_id"] = bounty_id
    return payload


def _is_skill_job(job: dict[str, Any]) -> bool:
    payload = job.get("local_payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    if not isinstance(payload, dict):
        return False
    return (
        job.get("execution_mode") == "local_pull"
        and payload.get("skill_slug") == SKILL_SLUG
    )


def _is_skill_runner(runner: dict[str, Any]) -> bool:
    if runner.get("skill_slug") == SKILL_SLUG:
        return True
    name = runner.get("name") or ""
    return isinstance(name, str) and name.startswith(f"{SKILL_SLUG}-")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manage seren-cron local-pull schedules for prophet-arb-bot."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create or update the runner and local-pull job.")
    create.add_argument("--name", default="", help="Job name. Defaults to prophet-arb-bot-{user_id_short}.")
    create.add_argument("--schedule", default=DEFAULT_CRON_EXPRESSION, help="Cron expression (default 0 */6 * * *).")
    create.add_argument("--timezone", default=DEFAULT_TIMEZONE_NAME, help="IANA timezone (default UTC).")
    create.add_argument("--config", default="config.json", help="Config path passed to agent.py.")
    create.add_argument("--prophet-email", required=True, help="The user's Prophet email (used by the OTP worker).")
    create.add_argument("--email-provider", choices=("gmail", "outlook"), default="gmail")
    create.add_argument("--bounty-id", default="", help="Optional bounty id; defaults to '' (no bounty join).")
    create.add_argument("--yes-live", action="store_true", help="Schedule live ticks. Without this flag, scheduled runs emit dry-run intents only.")
    create.add_argument("--dry-run", action="store_true", help="Force dry-run ticks (overrides --yes-live).")
    create.add_argument("--user-id-short", default="", help="Override the auto-derived user id suffix.")
    create.add_argument("--runner-name", default="", help="Override the runner name.")
    create.add_argument("--machine-label", default=current_machine_label())
    create.add_argument("--poll-interval-seconds", type=int, default=DEFAULT_POLL_INTERVAL_SECONDS)

    sub.add_parser("list", help="List local-pull jobs owned by this skill.")
    sub.add_parser("list-runners", help="List runners owned by this skill.")

    pause = sub.add_parser("pause")
    pause.add_argument("--job-id", required=True)

    resume = sub.add_parser("resume")
    resume.add_argument("--job-id", required=True)

    delete_job = sub.add_parser("delete")
    delete_job.add_argument("--job-id", required=True)

    delete_runner = sub.add_parser("delete-runner")
    delete_runner.add_argument("--runner-id", required=True)

    return parser.parse_args(argv)


def run_create(client: SerenCronClient, args: argparse.Namespace) -> dict[str, Any]:
    user_short = args.user_id_short.strip() or default_user_id_short()
    job_name = args.name.strip() or default_job_name(user_short)
    runner_name = args.runner_name.strip() or default_runner_name(args.machine_label)
    payload = build_local_payload(
        config_path=args.config,
        prophet_email=args.prophet_email,
        email_provider=args.email_provider,
        bounty_id=args.bounty_id.strip() or None,
        yes_live=args.yes_live,
        dry_run=args.dry_run,
    )
    return client.setup_local_pull_schedule(
        runner_name=runner_name,
        machine_label=args.machine_label,
        platform_label=current_platform_label(),
        poll_interval_seconds=args.poll_interval_seconds,
        job_name=job_name,
        cron_expression=args.schedule,
        timezone_name=args.timezone,
        local_payload=payload,
    )


def dispatch(client: SerenCronClient, args: argparse.Namespace) -> Any:
    cmd = args.command
    if cmd == "create":
        return run_create(client, args)
    if cmd == "list":
        return {"jobs": [j for j in client.list_jobs() if _is_skill_job(j)]}
    if cmd == "list-runners":
        return {"runners": [r for r in client.list_runners() if _is_skill_runner(r)]}
    if cmd == "pause":
        return client.pause_job(args.job_id)
    if cmd == "resume":
        return client.resume_job(args.job_id)
    if cmd == "delete":
        return client.delete_job(args.job_id)
    if cmd == "delete-runner":
        return client.delete_runner(args.runner_id)
    raise ValueError(f"unknown subcommand: {cmd!r}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = SerenCronClient(gateway=HttpGateway())
    try:
        result = dispatch(client, args)
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"status": "ok", "result": result}, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
