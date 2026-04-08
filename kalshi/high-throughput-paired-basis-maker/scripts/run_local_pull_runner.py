#!/usr/bin/env python3
"""Poll seren-cron local pull jobs for kalshi-high-throughput-paired-basis-maker."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from seren_client import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    current_machine_label,
    default_local_pull_runner_name,
    ensure_local_pull_runner,
    poll_local_pull_runner,
    submit_local_pull_result,
)

SKILL_SLUG = "kalshi-high-throughput-paired-basis-maker"
DEFAULT_RUN_TYPE = "trade"
MAX_TAIL_CHARS = 4000


def _safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Kalshi basis maker seren-cron local pull runner."
    )
    parser.add_argument("--config", default="config.json", help="Default config path.")
    parser.add_argument("--runner-id", default="", help="Existing seren-cron runner id.")
    parser.add_argument("--runner-name", default="", help="Optional runner name override.")
    parser.add_argument("--machine-label", default=current_machine_label(), help="Runner machine label.")
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help="Fallback poll cadence.",
    )
    parser.add_argument("--once", action="store_true", help="Poll once then exit.")
    return parser.parse_args()


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


def _build_command(local_payload: dict[str, Any], default_config: str) -> list[str]:
    command = [
        sys.executable,
        str(SCRIPT_DIR / "agent.py"),
        "--config",
        _safe_str(local_payload.get("config_path"), default_config) or default_config,
        "--run-type",
        _safe_str(local_payload.get("run_type"), DEFAULT_RUN_TYPE) or DEFAULT_RUN_TYPE,
    ]
    backtest_file = _safe_str(local_payload.get("backtest_file"), "")
    if backtest_file:
        command.extend(["--backtest-file", backtest_file])
    backtest_days = local_payload.get("backtest_days")
    if backtest_days is not None:
        command.extend(["--backtest-days", str(_safe_int(backtest_days, 0))])
    if local_payload.get("yes_live"):
        command.append("--yes-live")
    if local_payload.get("unwind_all"):
        command.append("--unwind-all")
    return command


def _response_body(command: list[str], stdout_text: str, stderr_text: str, exit_code: int) -> str:
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


def main() -> int:
    args = parse_args()
    try:
        runner_id = args.runner_id.strip()
        if not runner_id:
            runner = ensure_local_pull_runner(
                skill_slug=SKILL_SLUG,
                runner_name=args.runner_name.strip() or default_local_pull_runner_name(SKILL_SLUG, args.machine_label),
                machine_label=args.machine_label,
                poll_interval_seconds=args.poll_interval_seconds,
                timeout_seconds=30.0,
            )
            runner_id = _safe_str(runner.get("id"), "")
        last_seen_result_id: str | None = None

        while True:
            poll_result = poll_local_pull_runner(
                runner_id,
                last_seen_result_id=last_seen_result_id,
                timeout_seconds=30.0,
            )
            if _safe_str(poll_result.get("action"), "") != "run":
                if args.once:
                    print(json.dumps({"status": "ok", "runner_id": runner_id, "action": poll_result.get("action", "idle")}, sort_keys=True))
                    return 0
                time.sleep(max(1, _safe_int(poll_result.get("next_poll_seconds"), args.poll_interval_seconds)))
                continue

            job = _coerce_payload(poll_result.get("job"))
            local_payload = _coerce_payload(job.get("local_payload"))
            execution_result = _coerce_payload(poll_result.get("execution_result"))
            execution_result_id = _safe_str(execution_result.get("id"), "")
            if not execution_result_id:
                raise RuntimeError("seren-cron poll response did not include execution_result.id")

            command = _build_command(local_payload, args.config)
            completed = subprocess.run(
                command,
                cwd=str(SKILL_ROOT),
                capture_output=True,
                text=True,
            )
            stdout_tail = completed.stdout[-MAX_TAIL_CHARS:]
            stderr_tail = completed.stderr[-MAX_TAIL_CHARS:]
            submit_local_pull_result(
                runner_id,
                execution_result_id=execution_result_id,
                status="succeeded" if completed.returncode == 0 else "failed",
                response_body=_response_body(command, completed.stdout, completed.stderr, completed.returncode),
                exit_code=completed.returncode,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
                timeout_seconds=30.0,
            )
            last_seen_result_id = execution_result_id
            if args.once:
                return completed.returncode
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
