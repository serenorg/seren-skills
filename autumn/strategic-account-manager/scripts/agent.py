from __future__ import annotations

import argparse
import json
from pathlib import Path


CANONICAL_SECTIONS = [
    "Core Product Shape",
    "Modes And Workflow",
    "Artifact Types And Output Contracts",
    "Durable Memory Model",
    "Account Stakeholder Opportunity Schemas",
    "Decision Risk Open Loop Schemas",
    "Evidence And Audit Schemas",
    "Confidence Readiness And Freshness Gates",
    "Prep Packet Composition And Ordering",
    "Client Ready Packet Composition And Sanitization",
    "Live Copilot Interaction Contract",
    "Interrupt Scoring Thresholds And Suppression",
    "Operator Control Surface",
    "Prep To Live Handoff Rules",
    "Live To Post Meeting Reconciliation Rules",
    "Follow Up And Recap Generation Rules",
]

GUARDRAILS = [
    "Prefer consolidation over expansion.",
    "Stop expanding once additions are refinements rather than net-new behavior.",
    "Separate observed fact, inference, recommendation, and open question.",
    "Downgrade low-confidence claims into open questions or suppress them.",
    "Strip internal strategy language from client-ready output.",
]


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_result(config: dict) -> dict:
    mode = config.get("mode", "prep")
    return {
        "status": "ok",
        "skill": "strategic-account-manager",
        "mode": mode,
        "center_of_gravity": "prep",
        "canonical_sections": CANONICAL_SECTIONS,
        "guardrails": GUARDRAILS,
        "operator_controls": {
            "packet_depth": config.get("packet_depth", "standard"),
            "confidence_floor": config.get("confidence_floor", 7),
            "interruptiveness": config.get("interruptiveness", "medium"),
            "focus_mode": config.get("focus_mode", "general"),
        },
        "handoffs": {
            "prep_to_live": [
                "meeting_objective",
                "stakeholder_map",
                "opportunity_map",
                "risk_map",
                "open_questions",
                "recommended_talk_track",
            ],
            "live_to_post_meeting": [
                "commitments",
                "objections",
                "decision_changes",
                "risk_changes",
                "new_evidence",
                "next_steps",
            ],
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(Path(args.config))
    print(json.dumps(build_result(config), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
