"""Local-pull runner for pk-lead-intelligence (Phase 5 — issue #779).

Long-lived process that polls seren-cron, claims due ticks, dispatches
the work to `scripts/agent.py` as a subprocess, and submits the result
back to seren-cron. Auto-pauses the originating job on a publisher 402
(low SerenBucks) so a back-to-back retry loop cannot drain the
operator's prepaid balance.

The argv shape the subprocess receives is locked by
`tests/test_run_local_pull_runner.py`:

    python <agent.py> --config <path> --command <cmd> [flags…]

`flags` is the verbatim list the job's local_payload carries (e.g.
`["--batch", "--dry-run"]` or `["--allow-live"]`). The runner does not
re-interpret them — that keeps the live-mode gate purely on the
setup_cron side and means a flag rename in agent.py only requires
re-running `setup_cron create`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Make `from scripts.* import …` work when this file is launched as a
# script. Same pattern as agent.py.
_SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(_SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(_SKILL_ROOT))

from scripts.cron.seren_cron_client import (  # noqa: E402
    DEFAULT_POLL_INTERVAL_SECONDS,
    SerenCronClient,
    current_machine_label,
    current_platform_label,
    default_runner_name,
    detect_auto_pause_reason,
)


_AGENT_SCRIPT = _SKILL_ROOT / "scripts" / "agent.py"
_MAX_TAIL_CHARS = 4000


# --------------------------------------------------------------------- #
# Factory + parser                                                      #
# --------------------------------------------------------------------- #


def _build_client() -> SerenCronClient:
    """Seam tests monkeypatch to inject a fake client."""
    return SerenCronClient()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pk-lead-intelligence-runner",
        description=(
            "Poll seren-cron for pk-lead-intelligence work and dispatch "
            "each tick to scripts/agent.py as a subprocess."
        ),
    )
    parser.add_argument(
        "--runner-name",
        default="",
        help="Override the runner name; defaults to skill + machine.",
    )
    parser.add_argument(
        "--machine-label",
        default=current_machine_label(),
        help="Friendly machine label stored on the runner record.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Fallback poll cadence when seren-cron omits next_poll_seconds.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "Poll once, dispatch at most one job, then exit. Used by "
            "tests and operator-side smoke checks."
        ),
    )
    return parser


# --------------------------------------------------------------------- #
# Argv construction                                                     #
# --------------------------------------------------------------------- #


def _coerce_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _build_subprocess_argv(local_payload: dict[str, Any]) -> list[str]:
    """Compose the argv the runner subprocesses.

    Pinned shape: python <agent.py> --config <path> --command <cmd> <flags…>

    `command` and `flags` come straight from the job's local_payload so
    setup_cron is the only place that decides what the live/dry gates
    look like.
    """

    config_path = local_payload.get("config_path") or "config.json"
    command = local_payload.get("command")
    if not isinstance(command, str) or not command:
        raise RuntimeError(
            "seren-cron job local_payload missing `command` — refusing "
            "to subprocess an unspecified agent command."
        )
    flags = local_payload.get("flags") or []
    if not isinstance(flags, list):
        flags = []

    argv: list[str] = [
        sys.executable,
        str(_AGENT_SCRIPT),
        "--config",
        str(config_path),
        "--command",
        command,
    ]
    for flag in flags:
        if isinstance(flag, str) and flag:
            argv.append(flag)
    return argv


# --------------------------------------------------------------------- #
# Result body / submission                                              #
# --------------------------------------------------------------------- #


def _build_response_body(
    argv: list[str], stdout: str, stderr: str, exit_code: int
) -> str:
    """Compose the response_body the runner submits back to seren-cron.

    Prefer the subprocess stdout verbatim when it looks like JSON so the
    seren-cron execution_results table carries the agent's own
    structured output. Fall back to a wrapper envelope otherwise.
    """

    stripped = stdout.strip()
    if stripped:
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            return json.dumps(parsed, sort_keys=True, default=str)
    return json.dumps(
        {
            "status": "ok" if exit_code == 0 else "error",
            "command": argv,
            "exit_code": exit_code,
            "stdout": stdout[-_MAX_TAIL_CHARS:],
            "stderr": stderr[-_MAX_TAIL_CHARS:],
        },
        sort_keys=True,
    )


# --------------------------------------------------------------------- #
# Main loop                                                             #
# --------------------------------------------------------------------- #


def _ensure_runner_id(
    client: SerenCronClient, args: argparse.Namespace
) -> str:
    runner = client.ensure_runner(
        runner_name=args.runner_name.strip() or default_runner_name(args.machine_label),
        machine_label=args.machine_label,
        platform_label=current_platform_label(),
        poll_interval_seconds=args.poll_interval_seconds,
    )
    runner_id = runner.get("id") if isinstance(runner, dict) else None
    if not isinstance(runner_id, str) or not runner_id:
        raise RuntimeError("seren-cron did not return a runner id")
    return runner_id


def _execute_one_tick(
    client: SerenCronClient,
    runner_id: str,
    last_seen_result_id: str | None,
    args: argparse.Namespace,
) -> tuple[bool, int, str | None]:
    """Poll once. Return (dispatched, exit_code, new_last_seen_result_id).

    `dispatched=False` means the poll was idle; the loop should sleep.
    """

    poll = client.poll(runner_id, last_seen_result_id=last_seen_result_id)
    if poll.action != "run":
        return False, 0, last_seen_result_id

    job = _coerce_payload(poll.job)
    job_id = job.get("id") if isinstance(job.get("id"), str) else ""
    local_payload = _coerce_payload(job.get("local_payload"))
    execution_result_id = poll.execution_result_id
    if not execution_result_id:
        raise RuntimeError(
            "seren-cron poll returned action=run but no execution_result.id"
        )

    argv = _build_subprocess_argv(local_payload)
    completed = subprocess.run(
        argv,
        cwd=str(_SKILL_ROOT),
        capture_output=True,
        text=True,
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    exit_code = completed.returncode

    pause_reason = detect_auto_pause_reason(stdout, stderr)
    if pause_reason and job_id:
        try:
            client.pause_job(job_id)
        except Exception as pause_exc:  # noqa: BLE001
            # Never let an auto-pause failure mask the underlying tick
            # failure; the operator needs to see the 402 either way.
            stderr += f"\n[runner] auto-pause failed: {pause_exc!r}"

    client.submit_result(
        runner_id,
        execution_result_id=execution_result_id,
        status="succeeded" if exit_code == 0 else "failed",
        response_body=_build_response_body(argv, stdout, stderr, exit_code),
        exit_code=exit_code,
        stdout_tail=stdout[-_MAX_TAIL_CHARS:],
        stderr_tail=stderr[-_MAX_TAIL_CHARS:],
    )
    return True, exit_code, execution_result_id


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    client = _build_client()

    try:
        runner_id = _ensure_runner_id(client, args)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 1

    last_seen_result_id: str | None = None
    poll_interval = max(1, args.poll_interval_seconds)

    while True:
        try:
            dispatched, exit_code, last_seen_result_id = _execute_one_tick(
                client, runner_id, last_seen_result_id, args
            )
        except Exception as exc:  # noqa: BLE001
            print(
                json.dumps({"status": "error", "message": str(exc)}, sort_keys=True)
            )
            return 1

        if args.once:
            if dispatched:
                return exit_code
            print(
                json.dumps(
                    {"status": "ok", "runner_id": runner_id, "action": "idle"},
                    sort_keys=True,
                )
            )
            return 0

        if not dispatched:
            time.sleep(poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
