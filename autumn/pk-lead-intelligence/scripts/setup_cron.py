"""Manage the pk-lead-intelligence seren-cron schedule (Phase 5 — #779).

Two jobs ride the same runner:

* `pk-lead-intelligence-daily` — fires `agent.py --command run --batch`
  at 06:00 ET, weekdays. With `--allow-live` on this script, the job
  payload carries `--allow-live` so the runner subprocesses the live
  Note-write path. Without it, the payload carries `--dry-run`.

* `pk-lead-intelligence-weekly` — fires `agent.py --command weekly` at
  07:00 ET on Tuesdays. Same `--allow-live` / `--dry-run` split.

Re-running `create` for the same job kind is idempotent: the client
layer (`scripts.cron.seren_cron_client.SerenCronClient`) lists existing
jobs by name and PUTs in place rather than POSTing a duplicate.

Output is JSON on stdout — `{"status": "ok", "result": …}` on success,
`{"status": "error", "message": …}` on failure. Matches the shape
peer skills (prophet-arb-bot, polymarket-bot) emit so an operator can
pipe through `jq`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Make `from scripts.* import …` work when this file is launched as a
# script. Mirrors agent.py's path-nudge.
_SKILL_ROOT = str(Path(__file__).resolve().parent.parent)
if _SKILL_ROOT not in sys.path:
    sys.path.insert(0, _SKILL_ROOT)

from scripts.cron.seren_cron_client import (  # noqa: E402
    DEFAULT_POLL_INTERVAL_SECONDS,
    SKILL_SLUG,
    SerenCronClient,
    current_machine_label,
    current_platform_label,
    default_runner_name,
)


# --------------------------------------------------------------------- #
# Job kind defaults                                                     #
# --------------------------------------------------------------------- #


# Mirrors SKILL.md > Schedule. America/New_York is operator-local; the
# weekly cron at 07:00 ET fires before the Tuesday review at 09:00 ET.
_JOB_KINDS = {
    "daily": {
        "name_suffix": "daily",
        "cron_expression": "0 6 * * 1-5",
        "command": "run",
        "extra_flags": ["--batch"],
    },
    "weekly": {
        "name_suffix": "weekly",
        "cron_expression": "0 7 * * 2",
        "command": "weekly",
        "extra_flags": [],
    },
}
_DEFAULT_TIMEZONE = "America/New_York"


def _build_local_payload(
    *, job_kind: str, config_path: str, allow_live: bool
) -> dict[str, Any]:
    """Compose the per-tick payload the runner reads back from the job.

    `flags` carries the exact arg list the runner appends to
    `agent.py --command <command>`. We include `--dry-run` or
    `--allow-live` explicitly so the runner does not need to know the
    semantics of either gate — both come straight off the job record.
    """

    kind = _JOB_KINDS[job_kind]
    flags = list(kind["extra_flags"])
    flags.append("--allow-live" if allow_live else "--dry-run")
    return {
        "skill_slug": SKILL_SLUG,
        "command": kind["command"],
        "config_path": config_path,
        "flags": flags,
    }


def _build_client() -> SerenCronClient:
    """Factory the CLI uses. Tests monkeypatch this to inject a stub."""
    return SerenCronClient()


# --------------------------------------------------------------------- #
# Argument parser                                                       #
# --------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pk-lead-intelligence-setup-cron",
        description=(
            "Register the local-pull runner and upsert the daily + "
            "weekly jobs that drive pk-lead-intelligence on schedule."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser(
        "create",
        help="Register the runner and upsert one job (daily or weekly).",
    )
    create.add_argument(
        "--job",
        required=True,
        choices=sorted(_JOB_KINDS.keys()),
        help="Which job to upsert. Run this twice — once with `daily`, "
        "once with `weekly` — to schedule both.",
    )
    create.add_argument("--config", default="config.json")
    create.add_argument("--machine-label", default=current_machine_label())
    create.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
    )
    create.add_argument("--runner-name", default="")
    create.add_argument(
        "--allow-live",
        action="store_true",
        help=(
            "Schedule live ticks. Without this flag, scheduled runs "
            "use the `--dry-run` path of agent.py."
        ),
    )

    sub.add_parser("list", help="List jobs scoped to this skill.")
    sub.add_parser("list-runners", help="List runners scoped to this skill.")

    pause = sub.add_parser("pause")
    pause.add_argument("--job-id", required=True)

    resume = sub.add_parser("resume")
    resume.add_argument("--job-id", required=True)

    delete_job = sub.add_parser("delete")
    delete_job.add_argument("--job-id", required=True)

    delete_runner = sub.add_parser("delete-runner")
    delete_runner.add_argument("--runner-id", required=True)

    return parser


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


# --------------------------------------------------------------------- #
# Dispatch                                                              #
# --------------------------------------------------------------------- #


def _run_create(client: SerenCronClient, args: argparse.Namespace) -> Any:
    kind = _JOB_KINDS[args.job]
    job_name = f"{SKILL_SLUG}-{kind['name_suffix']}"
    runner_name = args.runner_name.strip() or default_runner_name(args.machine_label)
    payload = _build_local_payload(
        job_kind=args.job,
        config_path=args.config,
        allow_live=args.allow_live,
    )
    return client.setup_local_pull_schedule(
        runner_name=runner_name,
        machine_label=args.machine_label,
        platform_label=current_platform_label(),
        poll_interval_seconds=args.poll_interval_seconds,
        job_name=job_name,
        cron_expression=kind["cron_expression"],
        timezone_name=_DEFAULT_TIMEZONE,
        local_payload=payload,
    )


def _dispatch(client: SerenCronClient, args: argparse.Namespace) -> Any:
    if args.command == "create":
        return _run_create(client, args)
    if args.command == "list":
        return {"jobs": [j for j in client.list_jobs() if _is_skill_job(j)]}
    if args.command == "list-runners":
        return {
            "runners": [r for r in client.list_runners() if _is_skill_runner(r)]
        }
    if args.command == "pause":
        return client.pause_job(args.job_id)
    if args.command == "resume":
        return client.resume_job(args.job_id)
    if args.command == "delete":
        return client.delete_job(args.job_id)
    if args.command == "delete-runner":
        return client.delete_runner(args.runner_id)
    raise ValueError(f"unknown subcommand: {args.command!r}")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    client = _build_client()
    try:
        result = _dispatch(client, args)
    except Exception as exc:  # noqa: BLE001 — surface to operator JSON
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 1
    print(
        json.dumps({"status": "ok", "result": result}, sort_keys=True, default=str)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
