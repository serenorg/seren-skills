#!/usr/bin/env python3
"""Kalshi consensus divergence monitor runtime."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SKILL_SLUG = "kalshi-consensus-divergence-monitor"


def _bootstrap_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if path.exists():
        return path
    example_path = path.with_name("config.example.json")
    if example_path.exists():
        path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Kalshi consensus divergence monitor.")
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
        "contract": "KALSHI-DEMO-INFLATION",
        "title": "Will inflation surprise higher this release?",
        "state": "monitor",
        "divergence_bps": 220,
        "gap_view": "Not evaluated in this monitor.",
        "coil_view": "Not evaluated in this monitor.",
        "health_caveat": "Consensus divergence is strong, but macro confirmation belongs in the macro monitor.",
        "thesis": "Cross-venue pricing disagreement widened materially.",
        "key_risk": "Another venue may reprice first and remove the gap.",
        "next_check": "Compare against the previous ranked divergence scan.",
    }
    return {
        "run_status": "ok",
        "mode": mode,
        "generated_at": generated_at,
        "signal_health": {
            "overall": "healthy",
            "gap": "unknown",
            "coil": "unknown",
            "interpretation": "This monitor focuses on divergence ranking, not macro confirmation.",
        },
        "market_candidates": [candidate],
        "selected_trades": [],
        "watchlist": [candidate],
        "blocked_reasons": [],
        "rationale": ["Consensus disagreement is wide enough to rank and revisit."],
        "risk_note": "Pure divergence can disappear quickly if one venue reprices before the user acts.",
        "freshness": {
            "kalshi_oracle": generated_at,
            "gap": "unknown",
            "coil": "unknown",
        },
        "desktop_summary": {
            "style": "mini_research_note",
            "verdict": "Monitor — the divergence is notable enough to rank, not to treat as a complete thesis.",
            "what_happened": "Kalshi is pricing this contract differently from the comparison venue set. The disagreement is large enough to track over time.",
            "why_it_matters": "A widening spread can reveal mispricing, but it is only one piece of the decision stack.",
            "main_risk": "The spread may close before any other signal confirms it.",
            "next_action": "Track the divergence trend and pair it with the macro monitor before escalating conviction.",
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
