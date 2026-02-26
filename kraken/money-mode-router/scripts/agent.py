#!/usr/bin/env python3
"""Kraken Money Mode Router agent."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

from kraken_client import KrakenClient
from mode_engine import ModeEngine
from serendb_store import SerenDBStore


QUESTION_SET: List[Dict[str, Any]] = [
    {
        "key": "primary_goal",
        "prompt": "What is your primary goal right now?",
        "options": [
            ("move-money", "Move money and payments"),
            ("grow-portfolio", "Grow a long-term portfolio"),
            ("trade-markets", "Trade markets actively"),
            ("build-onchain", "Build on-chain positions"),
            ("automate", "Automate strategy execution"),
        ],
    },
    {
        "key": "time_horizon",
        "prompt": "What time horizon matters most?",
        "options": [
            ("today", "Today"),
            ("weeks", "This month"),
            ("months", "3-12 months"),
            ("years", "1+ years"),
        ],
    },
    {
        "key": "risk_level",
        "prompt": "What risk level fits you?",
        "options": [
            ("low", "Lower volatility"),
            ("medium", "Balanced"),
            ("high", "Higher risk/higher upside"),
        ],
    },
    {
        "key": "hands_on",
        "prompt": "How hands-on do you want to be?",
        "options": [
            ("hands-off", "Hands-off"),
            ("balanced", "Balanced"),
            ("hands-on", "Hands-on"),
        ],
    },
    {
        "key": "portfolio_focus",
        "prompt": "What product focus do you want?",
        "options": [
            ("multi-asset", "Multi-asset"),
            ("crypto", "Crypto-first"),
            ("equities", "Equities-forward"),
            ("payments", "Payments-first"),
        ],
    },
    {
        "key": "activity_frequency",
        "prompt": "How often will you use this flow?",
        "options": [
            ("daily", "Daily"),
            ("weekly", "Weekly"),
            ("monthly", "Monthly"),
        ],
    },
]


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def ask_questions_interactive() -> Dict[str, str]:
    answers: Dict[str, str] = {}
    print("\nKraken Money Mode Router\n")
    for question in QUESTION_SET:
        print(question["prompt"])
        for idx, (_, label) in enumerate(question["options"], start=1):
            print(f"  {idx}. {label}")

        while True:
            raw = input("Select option number: ").strip()
            if not raw:
                print("Please choose a number.")
                continue

            if not raw.isdigit():
                print("Numbers only.")
                continue

            selection = int(raw)
            if 1 <= selection <= len(question["options"]):
                value = question["options"][selection - 1][0]
                answers[question["key"]] = value
                print()
                break

            print("Selection out of range.")

    return answers


def load_answers_file(path: str) -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)

    if not isinstance(raw, dict):
        raise ValueError("answers file must contain a JSON object")

    answers = {str(k): str(v) for k, v in raw.items()}
    return answers


def validate_answers(answers: Dict[str, str]) -> None:
    valid_by_key = {q["key"]: {value for value, _ in q["options"]} for q in QUESTION_SET}

    missing = [key for key in valid_by_key if key not in answers]
    if missing:
        raise ValueError(f"Missing answers for: {', '.join(missing)}")

    invalid = []
    for key, value in answers.items():
        if key in valid_by_key and value not in valid_by_key[key]:
            invalid.append((key, value))

    if invalid:
        rendered = ", ".join([f"{key}={value}" for key, value in invalid])
        raise ValueError(f"Invalid answer values: {rendered}")


def format_report(
    session_id: str,
    recommendations: List[Dict[str, Any]],
    actions: List[str],
    mode_coverage: Dict[str, Any],
) -> str:
    primary = recommendations[0]
    backup = recommendations[1] if len(recommendations) > 1 else None

    positive_scores = sum(max(r["score"], 0.0) for r in recommendations)
    confidence = (primary["score"] / positive_scores) if positive_scores else 0.0

    lines = []
    lines.append("============================================================")
    lines.append("KRAKEN MONEY MODE ROUTER")
    lines.append("============================================================")
    lines.append("")
    lines.append(f"Session ID:   {session_id}")
    lines.append(f"Primary:      {primary['label']} ({primary['mode_id']})")
    lines.append(f"Backup:       {backup['label']} ({backup['mode_id']})" if backup else "Backup:       n/a")
    lines.append(f"Confidence:   {confidence:.1%}")
    lines.append("")
    lines.append("Why this mode:")
    if primary["reasons"]:
        for reason in primary["reasons"]:
            lines.append(f"  - {reason}")
    else:
        lines.append("  - Default mode selection (no scoring signal).")

    lines.append("")
    lines.append("Action plan:")
    for index, step in enumerate(actions, start=1):
        lines.append(f"  {index}. {step}")

    lines.append("")
    lines.append("API-backed mode coverage:")
    lines.append(
        f"  Publishers:  {', '.join(mode_coverage['available_publishers']) or 'none'}"
    )
    lines.append(
        "  Enabled:     "
        + (", ".join(mode_coverage["supported_modes"]) or "none")
    )
    lines.append("")
    lines.append("Primary mode API endpoints:")
    primary_endpoints = mode_coverage.get("supported_mode_endpoints", {}).get(primary["mode_id"], [])
    if primary_endpoints:
        for endpoint in primary_endpoints:
            method = endpoint.get("method", "GET")
            path = endpoint.get("path", "/")
            publisher = endpoint.get("publisher", "unknown")
            lines.append(f"  - {publisher} {method} {path}")
    else:
        lines.append("  - No endpoint catalog configured for this mode.")
    lines.append("")
    lines.append("============================================================")

    return "\n".join(lines)


def _get_seren_api_key() -> str | None:
    return os.getenv("SEREN_API_KEY") or os.getenv("API_KEY")


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _build_store_from_env() -> SerenDBStore:
    api_key = _get_seren_api_key()
    return SerenDBStore(
        api_key=api_key,
        project_name=os.getenv("SERENDB_PROJECT_NAME"),
        database_name=os.getenv("SERENDB_DATABASE"),
        branch_name=os.getenv("SERENDB_BRANCH"),
        project_region=os.getenv("SERENDB_REGION", "aws-us-east-1"),
        auto_create=_env_flag("SERENDB_AUTO_CREATE", default=True),
        mcp_command=os.getenv("SEREN_MCP_COMMAND", "seren-mcp"),
    )


def run_init_db(args: argparse.Namespace) -> int:
    load_dotenv()
    try:
        store = _build_store_from_env()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        store.ensure_schema()
    except Exception as exc:  # noqa: BLE001
        print(f"Router persistence failed via seren-mcp: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()

    print("SerenDB schema initialized via seren-mcp.")
    return 0


def run_recommend(args: argparse.Namespace) -> int:
    load_dotenv()

    config_path = args.config
    if not Path(config_path).exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    config = load_config(config_path)

    if args.answers_file and args.interactive:
        print("Use either --answers-file or --interactive, not both.", file=sys.stderr)
        return 1

    if not args.answers_file and not args.interactive:
        print("Use --interactive or provide --answers-file.", file=sys.stderr)
        return 1

    if args.answers_file:
        answers = load_answers_file(args.answers_file)
    else:
        answers = ask_questions_interactive()

    validate_answers(answers)

    session_id = str(uuid.uuid4())
    profile_name = args.profile_name

    try:
        store = _build_store_from_env()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        store.ensure_schema()
        store.create_session(session_id, profile_name)
        store.save_answers(session_id, answers)

        account_snapshot: Dict[str, Any] = {}
        api_key = _get_seren_api_key()

        if api_key:
            kraken = KrakenClient(
                api_key=api_key,
                base_url=os.getenv("SEREN_GATEWAY_BASE_URL", "https://api.serendb.com"),
                publisher=os.getenv("KRAKEN_SPOT_PUBLISHER", "kraken-spot-trading"),
                kraken_api_key=os.getenv("KRAKEN_API_KEY"),
                kraken_api_secret=os.getenv("KRAKEN_API_SECRET"),
            )
            account_snapshot = kraken.get_account_snapshot()
            store.save_event(session_id, "account_snapshot", account_snapshot)
        else:
            store.save_event(session_id, "account_snapshot", {"skipped": "SEREN_API_KEY not set"})

        engine = ModeEngine(config)
        ranked, mode_coverage = engine.recommend(answers)

        recommendations = [asdict(item) for item in ranked]
        store.save_recommendations(session_id, recommendations)

        primary_mode = recommendations[0]["mode_id"]
        action_plan = engine.build_action_plan(primary_mode)
        store.save_actions(session_id, primary_mode, action_plan)

        store.save_event(session_id, "mode_coverage", mode_coverage)
        store.save_event(
            session_id,
            "final_result",
            {
                "primary_mode": primary_mode,
                "backup_mode": recommendations[1]["mode_id"] if len(recommendations) > 1 else None,
                "answers": answers,
            },
        )

        report = format_report(
            session_id=session_id,
            recommendations=recommendations,
            actions=action_plan,
            mode_coverage=mode_coverage,
        )
        print(report)

        if args.json:
            result_payload = {
                "session_id": session_id,
                "answers": answers,
                "account_snapshot": account_snapshot,
                "recommendations": recommendations,
                "action_plan": action_plan,
                "mode_coverage": mode_coverage,
            }
            print(json.dumps(result_payload, indent=2))
    except Exception as exc:  # noqa: BLE001
        print(f"Router persistence failed via seren-mcp: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kraken Money Mode Router")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db = subparsers.add_parser("init-db", help="Create SerenDB tables")
    init_db.set_defaults(func=run_init_db)

    recommend = subparsers.add_parser("recommend", help="Run mode recommendation")
    recommend.add_argument("--config", required=True, help="Path to config JSON")
    recommend.add_argument("--answers-file", help="Path to answers JSON")
    recommend.add_argument("--interactive", action="store_true", help="Use interactive prompt")
    recommend.add_argument("--profile-name", default="default", help="Profile label stored in SerenDB")
    recommend.add_argument("--json", action="store_true", help="Print JSON payload")
    recommend.set_defaults(func=run_recommend)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
