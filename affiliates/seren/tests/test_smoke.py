from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
FIXTURE_DIR = Path(__file__).parent / "fixtures"

sys.path.insert(0, str(SCRIPTS_DIR))

from block import block_email  # noqa: E402
from bootstrap import bootstrap_auth_and_db, sync_affiliate_profile  # noqa: E402
from common import (  # noqa: E402
    DEFAULT_CONFIG,
    REQUIRED_PLACEHOLDERS,
    daily_cap_from_input,
    deep_merge,
    footer_missing_placeholders,
    is_valid_email,
    parse_pasted_contacts,
    require_approve_draft_json_pairing,
)
from draft import await_approval, draft_pitch  # noqa: E402
from ingest import enforce_daily_cap, filter_eligible, ingest_contacts, resolve_provider  # noqa: E402
from send import merge_and_send  # noqa: E402
from sync import select_program, sync_joined_programs  # noqa: E402
from validators import validate_tracked_link  # noqa: E402


def _config(**input_overrides) -> dict:
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg["inputs"].update(input_overrides)
    return cfg


def _read_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


# --- quick invariants (should run fast, no subprocess) ---


def test_validates_daily_cap_never_exceeds_twenty_five() -> None:
    cfg = _config(daily_cap=999)
    assert daily_cap_from_input(cfg) == 25


def test_daily_cap_lower_bound() -> None:
    cfg = _config(daily_cap=0)
    assert daily_cap_from_input(cfg) == 1


def test_rejects_approve_draft_without_json_output() -> None:
    cfg = _config(approve_draft=True, json_output=False)
    error = require_approve_draft_json_pairing(cfg)
    assert error is not None
    assert error["error_code"] == "approve_draft_without_json_output"


def test_accepts_approve_draft_when_json_output_true() -> None:
    cfg = _config(approve_draft=True, json_output=True)
    assert require_approve_draft_json_pairing(cfg) is None


def test_requires_sender_address_before_send() -> None:
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg["simulate"]["sender_address_missing"] = True
    os.environ["SEREN_API_KEY"] = "fake"
    try:
        result = sync_affiliate_profile(cfg)
    finally:
        os.environ.pop("SEREN_API_KEY", None)
    assert result["status"] == "error"
    assert result["error_code"] == "no_sender_address"


def test_enforces_unique_program_contact_dedupe() -> None:
    contacts = [
        {"email": "alice@example.com"},
        {"email": "bob@example.com"},
    ]
    result = filter_eligible(
        contacts=contacts,
        program_slug="sample-saas-alpha",
        already_sent_for_program={"alice@example.com"},
        unsubscribes=set(),
    )
    assert result["eligible_count"] == 1
    assert result["skipped_dedupe"] == 1
    assert [c["email"] for c in result["eligible"]] == ["bob@example.com"]


def test_blocks_send_when_email_in_unsubscribes() -> None:
    contacts = [
        {"email": "alice@example.com"},
        {"email": "bob@example.com"},
    ]
    result = filter_eligible(
        contacts=contacts,
        program_slug="sample-saas-alpha",
        already_sent_for_program=set(),
        unsubscribes={"alice@example.com"},
    )
    assert result["skipped_unsub"] == 1
    assert result["eligible_count"] == 1


def test_footer_contains_unsubscribe_link_sender_id_and_address() -> None:
    cfg = _config()
    program = {
        "program_slug": "sample-saas-alpha",
        "program_name": "SaaS Alpha",
        "partner_link_url": "https://example.com/ref",
    }
    result = draft_pitch(config=cfg, program=program, run_id="run-x")
    assert result["status"] == "ok"
    body = result["draft"]["body_template"]
    for placeholder in REQUIRED_PLACEHOLDERS:
        assert placeholder in body, f"missing placeholder: {placeholder}"
    assert footer_missing_placeholders(body) == []


# --- smoke invariants (end-to-end through the stubs) ---


def test_bootstraps_profile_then_registers_on_404() -> None:
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg["simulate"]["profile_missing"] = True
    os.environ["SEREN_API_KEY"] = "fake"
    try:
        auth = bootstrap_auth_and_db(cfg)
        assert auth["status"] == "ok"
        profile = sync_affiliate_profile(cfg)
    finally:
        os.environ.pop("SEREN_API_KEY", None)
    assert profile["status"] == "ok"
    assert profile["registered_this_run"] is True


def test_syncs_joined_programs_before_select_program() -> None:
    cfg = _config(program_slug="sample-saas-alpha")
    programs_result = sync_joined_programs(cfg)
    assert programs_result["status"] == "ok"
    assert programs_result["count"] == 2
    selection = select_program(cfg, programs_result["programs"])
    assert selection["status"] == "ok"
    assert selection["program"]["program_slug"] == "sample-saas-alpha"


def test_select_program_rejects_unknown_slug() -> None:
    cfg = _config(program_slug="does-not-exist")
    programs_result = sync_joined_programs(cfg)
    selection = select_program(cfg, programs_result["programs"])
    assert selection["status"] == "error"
    assert selection["error_code"] == "unknown_program_slug"


def test_drafts_pitch_and_blocks_send_until_approved() -> None:
    cfg = _config(approve_draft=False, json_output=False)
    program = {
        "program_slug": "sample-saas-alpha",
        "program_name": "SaaS Alpha",
        "program_description": "…",
        "partner_link_url": "https://example.com/r",
    }
    draft_result = draft_pitch(config=cfg, program=program, run_id="run-1")
    approval = await_approval(config=cfg, draft=draft_result["draft"])
    assert approval["status"] == "pending_approval"

    send_result = merge_and_send(
        config=cfg,
        run_id="run-1",
        profile={
            "agent_id": "agent-x",
            "display_name": "X",
            "sender_address": "1 Market St",
        },
        program=program,
        provider_used="gmail",
        draft=draft_result["draft"],
        sendable=[{"email": "alice@example.com", "display_name": "Alice"}],
        approval=approval,
    )
    assert send_result["status"] == "blocked"
    assert send_result["error_code"] == "awaiting_approval"


def test_gmail_preferred_when_both_authorized() -> None:
    cfg = _config(provider="auto")
    result = resolve_provider(cfg)
    assert result["status"] == "ok"
    assert result["provider_used"] == "gmail"
    assert result["resolution_mode"] == "auto"


def test_outlook_chosen_when_explicit() -> None:
    cfg = _config(provider="outlook")
    result = resolve_provider(cfg)
    assert result["status"] == "ok"
    assert result["provider_used"] == "outlook"


def test_hard_bounce_inserts_unsubscribe_row() -> None:
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg["simulate"]["hard_bounce_email"] = "alice@example.com"
    cfg["inputs"]["approve_draft"] = True
    cfg["inputs"]["json_output"] = True

    program = {
        "program_slug": "sample-saas-alpha",
        "program_name": "SaaS Alpha",
        "partner_link_url": "https://example.com/r",
    }
    draft_result = draft_pitch(config=cfg, program=program, run_id="run-bounce")
    approval = await_approval(config=cfg, draft=draft_result["draft"])
    send_result = merge_and_send(
        config=cfg,
        run_id="run-bounce",
        profile={
            "agent_id": "agent-x",
            "display_name": "X",
            "sender_address": "1 Market St",
        },
        program=program,
        provider_used="gmail",
        draft=draft_result["draft"],
        sendable=[
            {"email": "alice@example.com", "display_name": "Alice"},
            {"email": "bob@example.com", "display_name": "Bob"},
        ],
        approval=approval,
    )
    assert send_result["sent_count"] == 1
    bounced = [u["email"] for u in send_result["new_unsubscribes"]]
    assert bounced == ["alice@example.com"]
    assert send_result["new_unsubscribes"][0]["source"] == "hard_bounce"


def test_enforce_daily_cap_clips_to_remaining() -> None:
    eligible = [{"email": f"x{i}@example.com"} for i in range(8)]
    result = enforce_daily_cap(eligible=eligible, cap=10, already_sent_today=7)
    assert len(result["sendable"]) == 3
    assert result["clipped_count"] == 5


def test_ingest_pasted_parses_name_email_and_plain() -> None:
    cfg = _config(
        contacts_source="pasted",
        contacts='"Alice Chen" <alice@example.com>\nbob@example.com',
    )
    result = ingest_contacts(cfg)
    assert result["count"] == 2
    by_email = {c["email"]: c for c in result["contacts"]}
    assert by_email["alice@example.com"]["display_name"] == "Alice Chen"
    assert by_email["bob@example.com"]["display_name"] == ""


def test_parse_pasted_rejects_invalid() -> None:
    parsed = parse_pasted_contacts("not-an-email, alice@example.com, also bad")
    assert [c["email"] for c in parsed] == ["alice@example.com"]


def test_is_valid_email() -> None:
    assert is_valid_email("a@b.co")
    assert not is_valid_email("no-at-sign")
    assert not is_valid_email("")


def test_deep_merge_preserves_nested() -> None:
    merged = deep_merge(
        {"a": {"b": 1, "c": 2}, "z": 0},
        {"a": {"c": 99}, "new": True},
    )
    assert merged == {"a": {"b": 1, "c": 99}, "z": 0, "new": True}


def test_status_joins_local_distributions_with_live_stats() -> None:
    cfg = _config(
        command="run",
        program_slug="sample-saas-alpha",
        contacts="carol@example.com",
        approve_draft=True,
        json_output=True,
    )
    config_path = FIXTURE_DIR / "_live_config.json"
    config_path.write_text(json.dumps(cfg), encoding="utf-8")
    env = {**os.environ, "SEREN_API_KEY": "fake"}
    try:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "agent.py"), "--config", str(config_path)],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(SCRIPTS_DIR),
        )
    finally:
        config_path.unlink(missing_ok=True)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["run_status"] == "ok"
    assert payload["send"]["sent_count"] == 1
    assert payload["live"]["stats"]["clicks_today"] >= 0
    assert payload["live"]["source_of_truth"] == "seren-affiliates"


# --- fixture sanity (skillforge-generated fixtures remain structurally valid) ---


def test_happy_path_fixture_is_successful() -> None:
    payload = _read_fixture("happy_path.json")
    assert payload["status"] == "ok"
    assert payload["skill"] == "seren-affiliate"


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


def test_block_command_validates_email() -> None:
    cfg = _config(block_email="not-an-email")
    result = block_email(cfg)
    assert result["status"] == "error"
    assert result["error_code"] == "invalid_email"


def test_block_command_creates_operator_unsubscribe() -> None:
    cfg = _config(block_email="stop@example.com")
    result = block_email(cfg)
    assert result["status"] == "ok"
    assert result["unsubscribe"]["source"] == "operator_manual"


# --- Issue #404: tracked_link validator (defense-in-depth) ---


def test_validate_tracked_link_accepts_body_containing_link() -> None:
    result = validate_tracked_link(
        merged_body="Hi Alice, here is the link: https://example.com/r/x?ref=demo\nThanks",
        tracked_link="https://example.com/r/x?ref=demo",
    )
    assert result["status"] == "ok"


def test_validate_tracked_link_rejects_body_missing_link() -> None:
    result = validate_tracked_link(
        merged_body="Hi Alice, here is the link: https://evil.example.com/hallucinated",
        tracked_link="https://example.com/r/x?ref=demo",
    )
    assert result["status"] == "validation_failed"
    assert result["error_code"] == "tracked_link_missing"
    assert result["expected_tracked_link"] == "https://example.com/r/x?ref=demo"


def test_merge_and_send_fails_closed_when_draft_drops_partner_link_placeholder() -> None:
    cfg = deepcopy(DEFAULT_CONFIG)
    cfg["inputs"]["approve_draft"] = True
    cfg["inputs"]["json_output"] = True

    program = {
        "program_slug": "sample-saas-alpha",
        "program_name": "SaaS Alpha",
        "partner_link_url": "https://example.com/r/alpha?ref=demo",
    }
    draft_result = draft_pitch(config=cfg, program=program, run_id="run-404")
    assert draft_result["status"] == "ok"

    tampered_draft = dict(draft_result["draft"])
    tampered_draft["body_template"] = (
        "Hi {name},\n\nI wanted to share SaaS Alpha. "
        "Here is a link: https://wrong.example.com/fake\n\n"
        "---\n{sender_identity}\n{sender_address}\n"
        "Unsubscribe: {unsubscribe_link}\n"
    )
    approval = await_approval(config=cfg, draft=tampered_draft)

    send_result = merge_and_send(
        config=cfg,
        run_id="run-404",
        profile={
            "agent_id": "agent-x",
            "display_name": "X",
            "sender_address": "1 Market St",
        },
        program=program,
        provider_used="gmail",
        draft=tampered_draft,
        sendable=[{"email": "alice@example.com", "display_name": "Alice"}],
        approval=approval,
    )
    assert send_result["status"] == "validation_failed"
    assert send_result["error_code"] == "tracked_link_missing"
    assert send_result["sent_count"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
