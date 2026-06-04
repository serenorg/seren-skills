from __future__ import annotations

from datetime import date
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]


def _agent_module():
    spec = importlib.util.spec_from_file_location("estate_entity_compliance_watchdog_agent", SKILL_ROOT / "scripts" / "agent.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _config() -> dict:
    return json.loads((SKILL_ROOT / "config.example.json").read_text(encoding="utf-8"))


def test_exception_event_routes_to_review_gate() -> None:
    agent = _agent_module()
    result = agent.run_once(_config(), allow_live=False, today=date(2026, 6, 4))

    assert result["status"] == "attention_required"
    assert result["dry_run"] is True
    assert result["approval_required"] is True
    assert result["executable"] is False
    assert result["dry_run_to"] == "taariq@serendb.com"
    assert result["approval_tasks"][0]["exception_flags"] == _config()["operator_profile"]["exception_flags"]
    assert result["audit_events"][0]["request_key"].endswith(":dry-run")


def test_live_mode_requires_dual_gate() -> None:
    agent = _agent_module()
    config = _config()
    config["dry_run"] = False
    config["live_mode"] = True
    config["inputs"]["approval_confirmed"] = True

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
