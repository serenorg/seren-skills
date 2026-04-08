#!/usr/bin/env python3
"""CLI entrypoint for the claude/serendb-memory skill."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from memory_service import (
    MemorySyncError,
    MemorySyncService,
    LocalState,
    build_service_config,
    desktop_block_reason,
    ensure_cloud_client,
    load_json,
    write_default_config,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync Claude Code auto-memory into SerenDB without Seren Desktop.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default=None,
        help="install | start | stop | status | doctor | migrate | flush | uninstall | export",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to config file. Defaults to config.json in the current directory.",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="Run the watcher loop in the current process.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one sync cycle for start/migrate.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write exported memory snapshots.",
    )
    return parser.parse_args()


def _resolve_command(config_data: dict[str, Any], explicit_command: str | None) -> str:
    if explicit_command:
        return explicit_command
    inputs = config_data.get("inputs", {})
    return str(inputs.get("command", "status"))


def _load_or_bootstrap_config(config_path: Path, *, command: str) -> tuple[Path, dict[str, Any]]:
    if config_path.exists():
        return config_path, load_json(config_path)
    if command == "install":
        return config_path, load_json(write_default_config(config_path))
    return config_path, {}


def run_foreground_loop(service: MemorySyncService) -> dict[str, Any]:
    cycles = 0
    last_report: dict[str, Any] | None = None
    try:
        while True:
            cycles += 1
            last_report = service.sync_once()
            time.sleep(service.config.poll_interval_seconds)
    except KeyboardInterrupt:
        return {
            "status": "ok",
            "command": "start",
            "mode": "foreground",
            "cycles": cycles,
            "last_report": last_report or {},
            "stopped": "keyboard_interrupt",
        }


def run_once(
    *,
    config_path: str,
    command: str | None = None,
    foreground: bool = False,
    once: bool = False,
    output_dir: str | None = None,
) -> dict[str, Any]:
    blocked_by = desktop_block_reason()
    resolved_config_path = Path(config_path).resolve(strict=False)
    config_seed = load_json(resolved_config_path) if resolved_config_path.exists() else {}
    resolved_command = _resolve_command(config_seed, command)

    if blocked_by:
        return {
            "status": "error",
            "error_code": "desktop_not_supported",
            "message": (
                "This skill is only for non-SerenDesktop Claude Code users. "
                f"Detected Desktop runtime marker: {blocked_by}."
            ),
        }

    resolved_config_path, config_data = _load_or_bootstrap_config(
        resolved_config_path,
        command=resolved_command,
    )
    config = build_service_config(config_data)
    state = LocalState(config.state_dir, service_name=config.service_name)

    cloud = None
    api_key_source = None
    cloud_commands = {"install", "start", "migrate", "flush", "export"}
    if resolved_command in cloud_commands and not config.dry_run:
        cloud, api_key_source = ensure_cloud_client(config, state)

    service = MemorySyncService(config, cloud=cloud, state=state)

    if resolved_command == "install":
        sync_report = service.sync_once()
        service_result = None
        if config.install_service_on_install:
            service_result = service.install_service(
                config_path=resolved_config_path,
                python_executable=sys.executable,
                agent_path=Path(__file__).resolve(),
            )
        return {
            "status": "ok",
            "command": "install",
            "config_path": str(resolved_config_path),
            "api_key_source": api_key_source,
            "sync": sync_report,
            "service": service_result,
            "service_status": service.service_status(),
        }

    if resolved_command == "start":
        if foreground:
            if once:
                return {
                    "status": "ok",
                    "command": "start",
                    "mode": "once",
                    "report": service.sync_once(),
                }
            return run_foreground_loop(service)
        return {
            "status": "ok",
            "command": "start",
            "mode": "background",
            "service": service.start_service(),
        }

    if resolved_command == "stop":
        return {"status": "ok", "command": "stop", "service": service.stop_service()}

    if resolved_command == "status":
        return {
            "status": "ok",
            "command": "status",
            "config_path": str(resolved_config_path),
            "queue_count": state.queue_count(),
            "known_projects": len(state.known_projects()),
            "service": service.service_status(),
        }

    if resolved_command == "doctor":
        return service.doctor()

    if resolved_command == "migrate":
        return {
            "status": "ok",
            "command": "migrate",
            "report": service.sync_once(),
        }

    if resolved_command == "flush":
        report = service.flush_queue()
        report["rendered"] = service.render_all_indexes()
        return {"status": "ok", "command": "flush", "report": report}

    if resolved_command == "uninstall":
        return {
            "status": "ok",
            "command": "uninstall",
            "service": service.uninstall_service(),
        }

    if resolved_command == "export":
        target_dir = (
            Path(output_dir).resolve(strict=False)
            if output_dir
            else (config.state_dir / "exports" / str(int(time.time()))).resolve(strict=False)
        )
        export_result = service.export_memories(target_dir)
        export_result["command"] = "export"
        return export_result

    return {
        "status": "error",
        "error_code": "validation_error",
        "message": f"Unsupported command: {resolved_command}",
    }


def main() -> int:
    args = parse_args()
    try:
        result = run_once(
            config_path=args.config,
            command=args.command,
            foreground=args.foreground,
            once=args.once,
            output_dir=args.output_dir,
        )
    except MemorySyncError as exc:
        result = {
            "status": "error",
            "error_code": "runtime_error",
            "message": str(exc),
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
