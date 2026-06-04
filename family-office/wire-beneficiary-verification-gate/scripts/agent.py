#!/usr/bin/env python3
"""Dry-run-first operator runtime for wire-beneficiary-verification-gate."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


SKILL_NAME = "wire-beneficiary-verification-gate"
PILLAR = "complexity-management"
DEFAULT_DRY_RUN_TO = "taariq@serendb.com"
STATE_TABLES = [
    "beneficiary_register",
    "payment_screen_events",
    "verification_tasks",
    "halt_ledger",
    "fraud_flags",
]
AVAILABLE_CONNECTORS = [
    "browser",
    "cron",
    "gmail",
    "outlook",
    "passwords",
    "storage",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the wire verification gate.")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--functional-test", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    return parser.parse_args()


def load_config(config_path: str) -> Dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def run_once(
    config: Dict[str, Any],
    *,
    allow_live: bool = False,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    today = today or date.today()
    dry_run = bool(config.get("dry_run", True))
    live_mode = bool(config.get("live_mode", False))
    if not dry_run:
        if not allow_live:
            raise RuntimeError("live run requires --allow-live")
        if not live_mode:
            raise RuntimeError("--allow-live also requires live_mode=true in config.json")

    inputs = _inputs(config)
    instruction = _instruction(config, inputs)
    known_good = _find_known_good(
        config.get("known_good_register", []),
        str(instruction["counterparty_name"]),
    )
    halt_reasons = _halt_reasons(instruction, known_good)
    mode = "dry-run" if dry_run else "live"
    request_key = f"{instruction['instruction_id']}:{mode}"
    safe_to_pay = False
    if not halt_reasons:
        safe_to_pay = True
    elif (
        not dry_run
        and allow_live
        and live_mode
        and bool(inputs.get("allow_safe_to_pay", False))
    ):
        safe_to_pay = True

    verification_tasks = []
    if halt_reasons:
        verification_tasks.append(
            {
                "task_id": f"verify::{instruction['instruction_id']}",
                "instruction_id": instruction["instruction_id"],
                "counterparty_name": instruction["counterparty_name"],
                "reasons": halt_reasons,
                "callback_source": "known_good_register" if known_good else "operator_supplied_register",
                "callback_phone_hash": (known_good or {}).get("callback_phone_hash"),
                "required_action": (
                    "Call the known-good number on file. Never use the phone number "
                    "or reply-to address from the payment instruction."
                ),
                "status": "open",
            }
        )

    status = "halted" if halt_reasons and not safe_to_pay else "clear"
    if dry_run and status == "clear":
        status = "dry_run_ready"

    return {
        "skill": SKILL_NAME,
        "status": status,
        "dry_run": dry_run,
        "live_mode": live_mode,
        "as_of": today.isoformat(),
        "dry_run_to": _dry_run_to(config, inputs),
        "connectors": AVAILABLE_CONNECTORS,
        "schema_guard": _schema_guard(),
        "secrets_plan": _secrets_plan(config),
        "instruction": _redacted_instruction(instruction),
        "halt_reasons": halt_reasons,
        "safe_to_pay": safe_to_pay,
        "verification_tasks": verification_tasks,
        "audit_events": [
            {
                "request_key": request_key,
                "instruction_id": instruction["instruction_id"],
                "mode": mode,
                "status": status,
                "halt_reasons": halt_reasons,
            }
        ],
        "planned_actions": _planned_actions(dry_run, halt_reasons),
    }


def run_functional_test(config: Dict[str, Any]) -> Dict[str, Any]:
    dry_run_to = _dry_run_to(config, _inputs(config))
    sample = {
        "dry_run": True,
        "live_mode": False,
        "dry_run_to": dry_run_to,
        "inputs": {
            "instruction_id": "functional-wire-001",
            "counterparty_name": "Functional Test Fund",
        },
        "known_good_register": [
            {
                "counterparty_name": "Functional Test Fund",
                "bank_fingerprint": "bank:old",
                "callback_phone_hash": "phone:known-good",
                "last_verified_at": "2026-06-01",
            }
        ],
        "payment_instruction": {
            "instruction_id": "functional-wire-001",
            "counterparty_name": "Functional Test Fund",
            "bank_fingerprint": "bank:new",
            "amount_usd": 1000,
            "source": "functional-test",
        },
    }
    result = run_once(sample, allow_live=False, today=date(2026, 6, 4))
    checks = {
        "schema_guard": bool(result["schema_guard"]["tables"]),
        "passwords_secret_plan": result["secrets_plan"]["provider"] == "seren-passwords",
        "idempotency_key": result["audit_events"][0]["request_key"] == "functional-wire-001:dry-run",
        "approval_gate": result["status"] == "halted" and not result["safe_to_pay"],
        "dry_run_digest": result["dry_run_to"] == dry_run_to,
    }
    return {
        "skill": SKILL_NAME,
        "status": "all_green" if all(checks.values()) else "failed",
        "dry_run": True,
        "dry_run_to": dry_run_to,
        "checked_features": [name for name, ok in checks.items() if ok],
        "failed_features": [name for name, ok in checks.items() if not ok],
        "sample_result": result,
    }


def _inputs(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("inputs", {})
    return raw if isinstance(raw, dict) else {}


def _instruction(config: Dict[str, Any], inputs: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("payment_instruction", {})
    instruction = raw.copy() if isinstance(raw, dict) else {}
    instruction_id = instruction.get("instruction_id") or inputs.get("instruction_id")
    counterparty_name = instruction.get("counterparty_name") or inputs.get("counterparty_name")
    instruction["instruction_id"] = str(instruction_id or "unassigned-instruction")
    instruction["counterparty_name"] = str(counterparty_name or "unknown-counterparty")
    instruction["bank_fingerprint"] = str(instruction.get("bank_fingerprint") or "")
    return instruction


def _find_known_good(register: Any, counterparty_name: str) -> Optional[Dict[str, Any]]:
    if not isinstance(register, list):
        return None
    target = counterparty_name.casefold()
    for row in register:
        if not isinstance(row, dict):
            continue
        if str(row.get("counterparty_name", "")).casefold() == target:
            return row
    return None


def _halt_reasons(
    instruction: Dict[str, Any],
    known_good: Optional[Dict[str, Any]],
) -> List[str]:
    if known_good is None:
        return ["new_payee"]
    if not instruction.get("bank_fingerprint"):
        return ["missing_bank_fingerprint"]
    if instruction.get("bank_fingerprint") != known_good.get("bank_fingerprint"):
        return ["bank_fingerprint_changed"]
    return []


def _dry_run_to(config: Dict[str, Any], inputs: Dict[str, Any]) -> str:
    return str(config.get("dry_run_to") or inputs.get("dry_run_to") or DEFAULT_DRY_RUN_TO)


def _schema_guard() -> Dict[str, Any]:
    return {
        "database": "family_office_wire_beneficiary_verification_gate",
        "tables": STATE_TABLES,
        "runs_before_reads_or_writes": True,
        "create_if_missing": True,
    }


def _secrets_plan(config: Dict[str, Any]) -> Dict[str, Any]:
    raw = config.get("secrets", {})
    secrets = raw if isinstance(raw, dict) else {}
    return {
        "provider": "seren-passwords",
        "env_first": True,
        "vault_name": str(secrets.get("vault_name", "Family Office Operations")),
        "forbid_hardcoded_vault_ids": True,
        "forbid_glide_vault": True,
        "items": sorted(str(item) for item in secrets.get("items", [])),
    }


def _redacted_instruction(instruction: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "instruction_id": instruction["instruction_id"],
        "counterparty_name": instruction["counterparty_name"],
        "bank_fingerprint_present": bool(instruction.get("bank_fingerprint")),
        "amount_usd": instruction.get("amount_usd"),
        "source": instruction.get("source"),
    }


def _planned_actions(dry_run: bool, halt_reasons: List[str]) -> List[Dict[str, Any]]:
    actions = [
        {
            "action": "write_audit_event",
            "mode": "dry-run" if dry_run else "live",
            "approval_required": False,
        },
        {
            "action": "send_review_digest",
            "mode": "dry-run" if dry_run else "live",
            "approval_required": not dry_run,
        },
    ]
    if halt_reasons:
        actions.append(
            {
                "action": "halt_payment",
                "mode": "dry-run" if dry_run else "live",
                "approval_required": False,
            }
        )
    return actions


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if args.functional_test:
        result = run_functional_test(config)
    else:
        result = run_once(config, allow_live=args.allow_live)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
