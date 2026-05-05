from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SKILL_ROOT = Path(__file__).parent.parent


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_happy_path_fixture_is_successful() -> None:
    payload = _read_fixture("happy_path.json")
    assert payload["status"] == "ok"
    assert payload["skill"] == "seren-bucks-affiliates"
    assert payload["tracked_link"] == "https://serendb.com?ref=default"
    assert payload["program"]["program_id"] == "seren-bucks-default"
    assert payload["proposal"]["editable"] is True
    assert payload["proposal"]["quota_shortfall"] is True
    assert payload["proposal"]["qualified_count"] == 5
    assert payload["proposal"]["target"] == 10
    assert payload["proposal"]["sources_exhausted"] == [
        "gmail_sent",
        "outlook_sent",
        "gmail_contacts",
        "outlook_contacts",
    ]
    assert payload["limits"]["new_outbound_daily_cap"] == 10
    assert payload["limits"]["replies_count_against_daily_cap"] is False


def test_runtime_payload_matches_output_contract() -> None:
    env = dict(os.environ, API_KEY="test")
    output = subprocess.check_output(
        [
            "python3",
            str(SKILL_ROOT / "scripts" / "agent.py"),
            "--config",
            str(SKILL_ROOT / "config.example.json"),
            "--command",
            "run",
        ],
        env=env,
        text=True,
    )
    payload = json.loads(output)

    assert payload["tracked_link"] == "https://serendb.com?ref=default"
    assert payload["program"]["tracked_link"] == payload["tracked_link"]
    assert payload["candidate_sync"]["quota_shortfall"] is True
    assert payload["candidate_sync"]["qualified_count"] == 5
    assert payload["candidate_sync"]["target"] == 10
    assert payload["candidate_sync"]["sources_exhausted"] == [
        "gmail_sent",
        "outlook_sent",
        "gmail_contacts",
        "outlook_contacts",
    ]


def test_connector_failure_fixture_has_error_code() -> None:
    payload = _read_fixture("connector_failure.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "affiliate_bootstrap_failed"
    assert payload["retry_count"] == 3
    assert payload["fail_closed"] is True


def test_policy_violation_fixture_has_error_code() -> None:
    payload = _read_fixture("policy_violation.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "dnc_blocked"
    assert payload["signal"] == "unsubscribe"
    assert payload["hard_stop"] is True


def test_dry_run_fixture_blocks_live_execution() -> None:
    payload = _read_fixture("dry_run_guard.json")
    assert payload["dry_run"] is True
    assert payload["blocked_action"] == "send_without_approval"
    assert payload["approval_required"] is True
