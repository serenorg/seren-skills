#!/usr/bin/env python3
"""Generated SkillForge runtime for prophet-bounty-runner.

Phase 4 (TDD scaffolding): public surface declared as raising-stubs so the
red test suite collects cleanly. Phases 5–10 replace each stub with the real
implementation, flipping the corresponding tests green one at a time.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_DRY_RUN = True
AVAILABLE_CONNECTORS = ["bounty", "email_otp", "prophet", "storage"]


# ---------------------------------------------------------------------------
# Public surface — stubs to be replaced by phases 5–10.


def normalize_request(request: dict) -> dict:
    """Validate and normalize the user's input dict against the spec schema.

    Replaced in phase 5 (§11). Until then, every quick test fails here.
    """
    raise NotImplementedError("normalize_request not implemented (phase 5)")


def acquire_prophet_token_via_otp(
    email: str, *, provider: str, gateway: Any
) -> dict:
    """Drive the Privy email-OTP flow and return a JWT + viewer identity.

    Replaced in phase 5 (§11). Tests monkeypatch this symbol directly.
    """
    raise NotImplementedError("acquire_prophet_token_via_otp not implemented (phase 5)")


def run_command(request: dict, *, gateway: Any, storage: Any) -> dict:
    """Top-level entrypoint that the CLI and scheduled runs both call.

    Replaced in phase 10 (§16). Until then, every smoke test fails here.
    """
    raise NotImplementedError("run_command not implemented (phase 10)")


# ---------------------------------------------------------------------------
# CLI shim — placeholder until phase 10 wires run_command into main.


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


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    print(
        json.dumps(
            {
                "status": "stub",
                "connectors": AVAILABLE_CONNECTORS,
                "input_keys": sorted(config.get("inputs", {}).keys()),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
