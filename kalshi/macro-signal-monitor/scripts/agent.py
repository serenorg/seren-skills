#!/usr/bin/env python3
"""Kalshi macro signal monitor runtime."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SKILL_SLUG = "kalshi-macro-signal-monitor"


def _bootstrap_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if path.exists():
        return path
    example_path = path.with_name("config.example.json")
    if example_path.exists():
        path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Kalshi macro signal monitor.")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--mode", choices=("monitor", "report"), default="monitor")
    return parser.parse_args()


def load_config(config_path: str) -> dict[str, Any]:
    path = _bootstrap_config_path(config_path)
    path = _bootstrap_config_path(str(path))
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _timestamp() -> str:
    return datetime.now(tz=UTC).isoformat().replace("+00:00", "Z")


def build_result(mode: str) -> dict[str, Any]:
    generated_at = _timestamp()
    candidate = {
        "contract": "KALSHI-DEMO-GROWTH",
        "title": "Will growth expectations improve next month?",
        "state": "watchlist-only",
        "divergence_bps": 95,
        "gap_view": "Positive directional signal.",
        "coil_view": "Neutral-to-positive but less fresh than gap.",
        "health_caveat": "Signal freshness is uneven, so the contract stays watchlist-only.",
        "thesis": "Macro alignment is interesting, but not clean enough to escalate.",
        "key_risk": "One stale macro input can distort the picture.",
        "next_check": "Wait for both macro families to refresh.",
    }
    return {
        "run_status": "ok",
        "mode": mode,
        "generated_at": generated_at,
        "signal_health": {
            "overall": "ambiguous",
            "gap": "healthy",
            "coil": "stale",
            "interpretation": "Macro alignment exists, but freshness is not clean enough for a stronger state.",
        },
        "market_candidates": [candidate],
        "selected_trades": [],
        "watchlist": [candidate],
        "blocked_reasons": ["coil_freshness_is_stale"],
        "rationale": ["gap and coil do not have matching freshness quality, so confidence stays capped."],
        "risk_note": "A stale macro signal can create false confidence in an otherwise attractive contract.",
        "freshness": {
            "kalshi_oracle": generated_at,
            "gap": generated_at,
            "coil": "stale",
        },
        "desktop_summary": {
            "style": "mini_research_note",
            "verdict": "Watchlist-only — macro alignment exists, but the freshness is uneven.",
            "what_happened": "gap is supportive for this contract and coil is directionally similar, but coil is less fresh. That keeps the setup informative rather than actionable.",
            "why_it_matters": "The contract could strengthen if both signal families refresh in the same direction.",
            "main_risk": "The stale signal may be lagging the market rather than confirming it.",
            "next_action": "Keep the contract on the watchlist and wait for the next clean macro refresh.",
        },
        "audit": {
            "suite": "kalshi_signal_suite",
            "contract_version": "kalshi-shared-v1",
            "skill": SKILL_SLUG,
            "run_id": f"{SKILL_SLUG}-{generated_at}",
        },
    }


def main() -> int:
    if not os.environ.get("SEREN_API_KEY"):
        raise RuntimeError("Missing required SEREN_API_KEY. Set it before running the Kalshi skill suite.")
    args = parse_args()
    config = load_config(args.config)
    mode = str(config.get("inputs", {}).get("mode", args.mode))
    print(json.dumps(build_result(mode), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
