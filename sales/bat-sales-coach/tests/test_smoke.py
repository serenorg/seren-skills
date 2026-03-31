from __future__ import annotations

import json
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SKILL_PATH = Path(__file__).resolve().parents[1] / "SKILL.md"


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_happy_path_fixture_is_successful() -> None:
    payload = _read_fixture("happy_path.json")
    assert payload["status"] == "ok"
    assert payload["skill"] == "bat-sales-coach"


def test_connector_failure_fixture_has_error_code() -> None:
    payload = _read_fixture("connector_failure.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "connector_failure"


def test_policy_violation_fixture_has_error_code() -> None:
    payload = _read_fixture("policy_violation.json")
    assert payload["status"] == "error"
    assert payload["error_code"] == "policy_violation"


def test_dry_run_fixture_blocks_live_execution() -> None:
    payload = _read_fixture("dry_run_guard.json")
    assert payload["dry_run"] is True
    assert payload["blocked_action"] == "live_execution"


def test_skill_instructions_require_user_stated_dates() -> None:
    content = SKILL_PATH.read_text(encoding="utf-8")

    assert "## Date and Time Rules" in content
    assert "must not assume, compute, or suggest specific dates" in content
    assert "due date (user-stated only, never agent-computed)" in content
    assert "expected close date (user-stated only)" in content
    assert "What is the next behavior for this prospect, and when do you want to do it?" in content
