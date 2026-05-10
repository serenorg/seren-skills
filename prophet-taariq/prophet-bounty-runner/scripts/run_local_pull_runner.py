#!/usr/bin/env python3
"""Local-pull runner for prophet-bounty-runner.

Plan §18 (Phase 12). Long-lived process that polls seren-cron, claims
due jobs, and executes `agent.py --command run --json-output` with the
saved local payload. After each subprocess exit, inspects the output
for auto-pause signals (pool exhausted, low SerenBucks per §18.3) and
pauses the cron job before submitting the per-tick result.

The polling loop and the per-tick handler are split:
  - main() → wires args, gateway, runner registration, sleep loop
  - execute_one_tick() → pure handler tested in isolation
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from seren_cron_client import (  # noqa: E402
    DEFAULT_POLL_INTERVAL_SECONDS,
    PollResult,
    HttpGateway,
    SerenCronClient,
    current_machine_label,
    current_platform_label,
    default_runner_name,
    detect_auto_pause_reason,
)

MAX_TAIL_CHARS = 4000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="prophet-bounty-runner local-pull runner.")
    parser.add_argument("--config", default="config.json", help="Default config path when local payload omits one.")
    parser.add_argument("--runner-id", default="", help="Skip registration and reuse this runner id.")
    parser.add_argument("--runner-name", default="", help="Override the runner name.")
    parser.add_argument("--machine-label", default=current_machine_label())
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Fallback poll cadence when seren-cron does not echo next_poll_seconds.",
    )
    parser.add_argument("--once", action="store_true", help="Poll once, run at most one job, then exit.")
    return parser.parse_args(argv)


def build_agent_command(local_payload: dict[str, Any], default_config: str) -> list[str]:
    """Translate the saved local payload into `agent.py` argv.

    Plan §18.3 — every tick fires `scripts/agent.py --command run
    --json-output` with the user's saved inputs (prophet_email,
    email_provider, optional bounty_id / limits / dry_run flag).
    """
    cmd = [
        sys.executable,
        str(SCRIPT_DIR / "agent.py"),
        "--config",
        str(local_payload.get("config_path") or default_config),
        "--command",
        str(local_payload.get("command") or "run"),
        "--json-output",
    ]
    prophet_email = local_payload.get("prophet_email")
    if prophet_email:
        cmd.extend(["--prophet-email", str(prophet_email)])
    email_provider = local_payload.get("email_provider")
    if email_provider:
        cmd.extend(["--email-provider", str(email_provider)])
    bounty_id = local_payload.get("bounty_id")
    if bounty_id:
        cmd.extend(["--bounty-id", str(bounty_id)])
    candidate_limit = local_payload.get("candidate_limit")
    if isinstance(candidate_limit, int):
        cmd.extend(["--candidate-limit", str(candidate_limit)])
    submit_limit = local_payload.get("submit_limit")
    if isinstance(submit_limit, int):
        cmd.extend(["--submit-limit", str(submit_limit)])
    if local_payload.get("dry_run"):
        cmd.append("--dry-run")
    return cmd


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


def _response_body(command: list[str], stdout_text: str, stderr_text: str, exit_code: int) -> str:
    """Return the response_body string we hand back to seren-cron.

    If agent.py emitted a parseable JSON object, forward it verbatim;
    otherwise wrap the tails so the operator dashboard always has
    something structured to render.
    """
    stripped = (stdout_text or "").strip()
    if stripped:
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            return json.dumps(parsed, sort_keys=True)
    return json.dumps(
        {
            "status": "ok" if exit_code == 0 else "error",
            "command": command,
            "exit_code": exit_code,
            "stdout": (stdout_text or "")[-MAX_TAIL_CHARS:],
            "stderr": (stderr_text or "")[-MAX_TAIL_CHARS:],
        },
        sort_keys=True,
    )


def execute_one_tick(
    poll_result: PollResult,
    *,
    client: SerenCronClient,
    runner_id: str,
    default_config: str,
    skill_root: Path = SKILL_ROOT,
    subprocess_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict[str, Any]:
    """Process a single seren-cron poll response.

    Pure-ish handler tested in isolation by Phase 12's runner-dispatch
    tests. Returns a summary dict; the main loop uses the `action` and
    `exit_code` keys to decide whether to keep polling or exit.
    """
    if poll_result.action != "run":
        return {"action": poll_result.action or "idle"}

    if not poll_result.execution_result_id:
        raise RuntimeError("seren-cron poll response did not include execution_result.id")

    job = poll_result.job
    job_id = str(job.get("id") or "")
    local_payload = _coerce_payload(job.get("local_payload"))

    command = build_agent_command(local_payload, default_config)
    completed = subprocess_runner(
        command,
        cwd=str(skill_root),
        capture_output=True,
        text=True,
    )
    stdout_text = getattr(completed, "stdout", "") or ""
    stderr_text = getattr(completed, "stderr", "") or ""
    exit_code = int(getattr(completed, "returncode", 0) or 0)

    # Plan §18.3: pause the cron job BEFORE submitting the result if the
    # tick output reports pool exhaustion or a low-SerenBucks 402. The
    # pause must precede the submit so the dashboard reflects the new
    # paused state on the same tick.
    pause_reason = detect_auto_pause_reason(stdout_text, stderr_text)
    paused = False
    if pause_reason and job_id:
        client.pause_job(job_id)
        paused = True

    status = "succeeded" if exit_code == 0 and not pause_reason else "failed"
    client.submit_result(
        runner_id,
        execution_result_id=poll_result.execution_result_id,
        status=status,
        response_body=_response_body(command, stdout_text, stderr_text, exit_code),
        exit_code=exit_code,
        stdout_tail=stdout_text[-MAX_TAIL_CHARS:],
        stderr_tail=stderr_text[-MAX_TAIL_CHARS:],
    )

    return {
        "action": "run",
        "command": command,
        "exit_code": exit_code,
        "execution_result_id": poll_result.execution_result_id,
        "auto_pause_reason": pause_reason,
        "paused": paused,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    client = SerenCronClient(gateway=HttpGateway())

    try:
        runner_id = args.runner_id.strip()
        if not runner_id:
            runner = client.ensure_runner(
                runner_name=args.runner_name.strip() or default_runner_name(args.machine_label),
                machine_label=args.machine_label,
                platform_label=current_platform_label(),
                poll_interval_seconds=args.poll_interval_seconds,
            )
            runner_id = str(runner.get("id") or "")
            if not runner_id:
                raise RuntimeError("seren-cron returned a runner without an id.")

        last_seen_result_id: str | None = None
        while True:
            poll_result = client.poll(runner_id, last_seen_result_id=last_seen_result_id)
            if poll_result.action != "run":
                if args.once:
                    print(json.dumps({"status": "ok", "runner_id": runner_id, "action": poll_result.action or "idle"}, sort_keys=True))
                    return 0
                time.sleep(max(1, poll_result.next_poll_seconds))
                continue

            tick = execute_one_tick(
                poll_result,
                client=client,
                runner_id=runner_id,
                default_config=args.config,
            )
            last_seen_result_id = tick["execution_result_id"]
            if args.once:
                return tick.get("exit_code", 0) or 0
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
