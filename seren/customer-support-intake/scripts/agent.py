#!/usr/bin/env python3
"""Generated SkillForge runtime for customer-support-intake."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


DEFAULT_DRY_RUN = True
AVAILABLE_CONNECTORS = ['diagnostics', 'playwright', 'storage']


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run generated SkillForge agent runtime.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to runtime config file (default: config.json).",
    )
    return parser.parse_args()


def load_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def run_once(config: dict, dry_run: bool) -> dict:
    return {
        "status": "ok",
        "dry_run": dry_run,
        "connectors": AVAILABLE_CONNECTORS,
        "input_keys": sorted(config.get("inputs", {}).keys()),
    }


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    dry_run = bool(config.get("dry_run", DEFAULT_DRY_RUN))
    result = run_once(config=config, dry_run=dry_run)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
