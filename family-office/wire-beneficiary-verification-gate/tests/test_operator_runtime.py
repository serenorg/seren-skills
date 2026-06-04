from __future__ import annotations

from datetime import date
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]


def _agent_module():
    spec = importlib.util.spec_from_file_location(
        "wire_beneficiary_agent",
        SKILL_ROOT / "scripts" / "agent.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _screening_config() -> dict:
    return {
        "dry_run": True,
        "live_mode": False,
        "dry_run_to": "taariq@serendb.com",
        "inputs": {
            "instruction_id": "wire-20260604-001",
            "counterparty_name": "Acme Growth Fund",
        },
        "known_good_register": [
            {
                "counterparty_name": "Acme Growth Fund",
                "bank_fingerprint": "bank:old",
                "callback_phone_hash": "phone:known-good",
                "last_verified_at": "2026-05-01",
            }
        ],
        "payment_instruction": {
            "instruction_id": "wire-20260604-001",
            "counterparty_name": "Acme Growth Fund",
            "bank_fingerprint": "bank:new",
            "amount_usd": 125000,
            "source": "gmail",
        },
    }


def test_changed_bank_instruction_halts_and_opens_verification_task() -> None:
    agent = _agent_module()
    result = agent.run_once(_screening_config(), allow_live=False, today=date(2026, 6, 4))

    assert result["status"] == "halted"
    assert result["dry_run"] is True
    assert result["safe_to_pay"] is False
    assert result["dry_run_to"] == "taariq@serendb.com"
    assert result["halt_reasons"] == ["bank_fingerprint_changed"]
    assert result["verification_tasks"][0]["callback_source"] == "known_good_register"
    assert result["audit_events"][0]["request_key"] == "wire-20260604-001:dry-run"


def test_live_safe_to_pay_requires_dual_gate() -> None:
    agent = _agent_module()
    config = _screening_config()
    config["dry_run"] = False
    config["live_mode"] = True
    config["inputs"]["allow_safe_to_pay"] = True

    try:
        agent.run_once(config, allow_live=False, today=date(2026, 6, 4))
    except RuntimeError as exc:
        assert "--allow-live" in str(exc)
    else:
        raise AssertionError("live run without --allow-live must fail")

    config["live_mode"] = False
    try:
        agent.run_once(config, allow_live=True, today=date(2026, 6, 4))
    except RuntimeError as exc:
        assert "live_mode=true" in str(exc)
    else:
        raise AssertionError("--allow-live without live_mode=true must fail")


def test_functional_dry_run_cli_is_all_green() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "agent.py"),
            "--functional-test",
            "--config",
            str(SKILL_ROOT / "config.example.json"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result["status"] == "all_green"
    assert result["dry_run"] is True
    assert result["dry_run_to"] == "taariq@serendb.com"
    assert result["checked_features"] == [
        "schema_guard",
        "passwords_secret_plan",
        "idempotency_key",
        "approval_gate",
        "dry_run_digest",
    ]
