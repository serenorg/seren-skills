#!/usr/bin/env python3
"""Poll seren-cron local pull jobs for kalshi-bot."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from seren_client import SerenClient

SKILL_SLUG = "kalshi-bot"
DEFAULT_POLL_INTERVAL_SECONDS = 30
MAX_TAIL_CHARS = 4000


def _safe_str(value: Any, default: str = '') -> str:
    if value is None:
        return default
    return str(value).strip() or default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_payload(value: Any) -> Dict[str, Any]:
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


def _current_machine_label() -> str:
    import platform
    import socket
    hostname = socket.gethostname().split('.')[0]
    return f"{hostname}-{platform.system().lower()}"


def _default_runner_name(machine_label: str = '') -> str:
    label = machine_label or _current_machine_label()
    return f"{SKILL_SLUG}-{label}"


def _build_command(local_payload: Dict[str, Any], default_config: str) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "agent.py"),
        "--config",
        _safe_str(local_payload.get("config_path"), default_config) or default_config,
    ]
    if local_payload.get("dry_run", True):
        command.append("--dry-run")
    else:
        command.append("--yes-live")
    command.append("--once")
    return command


def _response_body(
    command: list[str],
    stdout_text: str,
    stderr_text: str,
    exit_code: int,
) -> str:
    stripped = stdout_text.strip()
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
            "stdout": stdout_text[-MAX_TAIL_CHARS:],
            "stderr": stderr_text[-MAX_TAIL_CHARS:],
        },
        sort_keys=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the kalshi-bot seren-cron local pull runner."
    )
    parser.add_argument(
        "--config", default="config.json",
        help="Default config path when local payload omits one.",
    )
    parser.add_argument(
        "--runner-id", default="",
        help="Existing seren-cron runner id.",
    )
    parser.add_argument(
        "--runner-name", default="",
        help="Optional runner name override.",
    )
    parser.add_argument(
        "--machine-label", default=_current_machine_label(),
        help="Friendly runner machine label.",
    )
    parser.add_argument(
        "--poll-interval-seconds", type=int,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Fallback poll cadence.",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Poll once, execute one job if available, then exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        client = SerenClient()
    except ValueError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 1

    try:
        runner_id = args.runner_id.strip()

        if not runner_id:
            # Register runner
            runner_name = (
                args.runner_name.strip()
                or _default_runner_name(args.machine_label)
            )
            runner_body = {
                'name': runner_name,
                'skill_slug': SKILL_SLUG,
                'machine_label': args.machine_label,
                'poll_interval_seconds': args.poll_interval_seconds,
            }
            runner = client.call_publisher(
                publisher='seren-cron',
                method='POST',
                path='/api/v1/runners',
                body=runner_body,
            )
            runner_id = _safe_str(runner.get('id', runner.get('runner_id')), '')

        last_seen_result_id: str | None = None

        while True:
            # Poll for work
            poll_result = client.call_publisher(
                publisher='seren-cron',
                method='POST',
                path=f'/api/v1/runners/{runner_id}/poll',
                body={'last_seen_result_id': last_seen_result_id},
            )

            if _safe_str(poll_result.get('action'), '') != 'run':
                if args.once:
                    print(json.dumps({
                        "status": "ok",
                        "runner_id": runner_id,
                        "action": poll_result.get("action", "idle"),
                    }, sort_keys=True))
                    return 0
                time.sleep(max(
                    1,
                    _safe_int(
                        poll_result.get('next_poll_seconds'),
                        args.poll_interval_seconds,
                    ),
                ))
                continue

            # Execute the job
            job = _coerce_payload(poll_result.get('job'))
            local_payload = _coerce_payload(job.get('local_payload'))
            execution_result = _coerce_payload(poll_result.get('execution_result'))
            execution_result_id = _safe_str(execution_result.get('id'), '')

            if not execution_result_id:
                raise RuntimeError(
                    "seren-cron poll response did not include execution_result.id"
                )

            command = _build_command(local_payload, args.config)
            completed = subprocess.run(
                command,
                cwd=str(SKILL_ROOT),
                capture_output=True,
                text=True,
            )

            # Submit result
            result_body = {
                'execution_result_id': execution_result_id,
                'status': 'succeeded' if completed.returncode == 0 else 'failed',
                'response_body': _response_body(
                    command,
                    completed.stdout,
                    completed.stderr,
                    completed.returncode,
                ),
                'exit_code': completed.returncode,
                'stdout_tail': completed.stdout[-MAX_TAIL_CHARS:],
                'stderr_tail': completed.stderr[-MAX_TAIL_CHARS:],
            }
            client.call_publisher(
                publisher='seren-cron',
                method='POST',
                path=f'/api/v1/runners/{runner_id}/results',
                body=result_body,
            )

            last_seen_result_id = execution_result_id

            if args.once:
                return completed.returncode

    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
