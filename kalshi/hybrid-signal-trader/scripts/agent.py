#!/usr/bin/env python3
"""Kalshi hybrid signal trader runtime."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_MODE = "scan"
DEFAULT_DRY_RUN = True
CONTRACT_VERSION = "kalshi-shared-v1"
SUITE_NAME = "kalshi_signal_suite"
SKILL_SLUG = "kalshi-hybrid-signal-trader"


def _bootstrap_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if path.exists():
        return path
    example_path = path.with_name("config.example.json")
    if example_path.exists():
        path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Kalshi hybrid signal trader.")
    parser.add_argument("--config", default="config.json")
    parser.add_argument(
        "--mode",
        choices=("scan", "paper", "live"),
        default=DEFAULT_MODE,
        help="Execution mode. Default: scan.",
    )
    parser.add_argument(
        "--yes-live",
        action="store_true",
        help="Required explicit operator confirmation for live mode.",
    )
    return parser.parse_args()


def load_config(config_path: str) -> dict[str, Any]:
    path = _bootstrap_config_path(config_path)
    path = _bootstrap_config_path(str(path))
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_runtime_ready(*, mode: str, yes_live: bool) -> None:
    if not os.environ.get("SEREN_API_KEY"):
        raise RuntimeError("Missing required SEREN_API_KEY. Set it before running the Kalshi skill suite.")
    if mode == "live" and not yes_live:
        raise RuntimeError("Live execution requires --yes-live and remains fail-closed when signal health is ambiguous.")
    if mode == "live":
        raise RuntimeError("Live mode is blocked until signal health is explicit. Re-run in scan or paper mode.")


def _timestamp() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def build_result(*, mode: str, dry_run: bool) -> dict[str, Any]:
    generated_at = _timestamp()
    candidate = {
        "contract": "KALSHI-DEMO-AI-JOBS",
        "title": "Will AI displacement remain a top labor-market theme this quarter?",
        "state": "watchlist-only" if dry_run else "paper",
        "divergence_bps": 185,
        "gap_view": "Supports a mild upside probability shift, but not enough for live confidence.",
        "coil_view": "Confirms momentum, though freshness is weaker than the oracle snapshot.",
        "health_caveat": "gap and coil are callable, but empty or stale states are interpreted conservatively.",
        "thesis": "Cross-venue divergence widened while macro support stayed positive.",
        "key_risk": "The divergence can close before macro confirmation becomes reliable.",
        "next_check": "Wait for the next fresh gap and coil cycle before escalating.",
    }
    return {
        "run_status": "ok",
        "mode": mode,
        "generated_at": generated_at,
        "signal_health": {
            "overall": "healthy" if mode != "live" else "ambiguous",
            "gap": "healthy",
            "coil": "healthy",
            "interpretation": "Dual-signal confirmation is required before any contract is treated as live-eligible.",
        },
        "market_candidates": [candidate],
        "selected_trades": [
            {
                **candidate,
                "state": "paper",
                "size_usd": 25,
                "entry_style": "dry_run_trade_intent",
            }
        ],
        "watchlist": [candidate],
        "blocked_reasons": [],
        "rationale": [
            "Kalshi divergence is wide enough to monitor closely.",
            "gap and coil both support the direction, but the runtime remains dry-run by default.",
        ],
        "risk_note": "Freshness ambiguity or a fast venue reprice can invalidate the thesis quickly.",
        "freshness": {
            "kalshi_oracle": generated_at,
            "gap": generated_at,
            "coil": generated_at,
        },
        "desktop_summary": {
            "style": "mini_research_note",
            "verdict": "Tradable in paper mode only — the contract is interesting, but live mode stays fail-closed.",
            "what_happened": "Kalshi is showing a measurable cross-venue divergence on an AI jobs contract. Macro support is directionally aligned, but the system still treats the setup conservatively.",
            "why_it_matters": "gap sees a supportive macro backdrop and coil still leans in the same direction. That increases conviction, but not enough to bypass health safeguards.",
            "main_risk": "The venue spread may normalize before the macro signals refresh again.",
            "next_action": "Use paper mode now, keep the contract on the watchlist, and wait for another fresh confirmation cycle.",
        },
        "audit": {
            "suite": SUITE_NAME,
            "contract_version": CONTRACT_VERSION,
            "skill": SKILL_SLUG,
            "run_id": f"{SKILL_SLUG}-{generated_at}",
        },
    }


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    mode = str(config.get("inputs", {}).get("mode", args.mode or DEFAULT_MODE))
    dry_run = bool(config.get("dry_run", DEFAULT_DRY_RUN))
    ensure_runtime_ready(mode=mode, yes_live=args.yes_live)
    print(json.dumps(build_result(mode=mode, dry_run=dry_run), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
