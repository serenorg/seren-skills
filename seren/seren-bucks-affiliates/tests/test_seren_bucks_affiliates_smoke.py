from __future__ import annotations

import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_happy_path_fixture_is_successful() -> None:
    payload = _read_fixture("happy_path.json")
    assert payload["status"] == "ok"
    assert payload["skill"] == "seren-bucks-affiliates"
    assert payload["program"]["program_id"] == "seren-bucks-default"
    assert payload["proposal"]["editable"] is True
    assert payload["limits"]["new_outbound_daily_cap"] == 10
    assert payload["limits"]["replies_count_against_daily_cap"] is False


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
