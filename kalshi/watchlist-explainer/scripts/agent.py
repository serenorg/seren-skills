#!/usr/bin/env python3
"""Kalshi watchlist explainer runtime."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SKILL_SLUG = "kalshi-watchlist-explainer"


def _bootstrap_config_path(config_path: str) -> Path:
    path = Path(config_path)
    if path.exists():
        return path
    example_path = path.with_name("config.example.json")
    if example_path.exists():
        path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Kalshi watchlist explainer.")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--mode", choices=("watchlist", "monitor"), default="watchlist")
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
    watch = {
        "contract": "KALSHI-DEMO-RATES",
        "title": "Will rate-cut odds rise this month?",
        "state": "watchlist-only",
        "divergence_bps": 110,
        "gap_view": "Macro surprise is supportive but incomplete.",
        "coil_view": "Momentum confirms only partially.",
        "health_caveat": "Single-family support is not enough for a trade intent.",
        "thesis": "Interesting consensus break, but confirmation is incomplete.",
        "key_risk": "A second signal family may never confirm.",
        "next_check": "Recheck after the next gap and coil refresh.",
    }
    return {
        "run_status": "ok",
        "mode": mode,
        "generated_at": generated_at,
        "signal_health": {
            "overall": "healthy",
            "gap": "healthy",
            "coil": "healthy",
            "interpretation": "Watchlist mode is designed to explain near-miss contracts clearly.",
        },
        "market_candidates": [watch],
        "selected_trades": [],
        "watchlist": [watch],
        "blocked_reasons": ["single_family_confirmation_only"],
        "rationale": ["The contract is interesting, but the evidence is incomplete."],
        "risk_note": "Near-miss setups can decay into noise if the second signal family never confirms.",
        "freshness": {
            "kalshi_oracle": generated_at,
            "gap": generated_at,
            "coil": generated_at,
        },
        "desktop_summary": {
            "style": "mini_research_note",
            "verdict": "Watchlist-only — the contract is interesting but not actionable yet.",
            "what_happened": "Kalshi moved enough to make the contract worth tracking, but the evidence is still incomplete.",
            "why_it_matters": "gap leans supportive and coil is only partially aligned. That is useful context, not a trade trigger.",
            "main_risk": "The setup may weaken before confirmation improves.",
            "next_action": "Keep it on the watchlist and wait for a fresh dual-signal check.",
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
