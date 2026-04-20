"""Critical tests for the comms push functions on the reference leaf.

Covers push_to_outlook_email, push_to_gmail, push_to_gcalendar. One reference
leaf tests the contract because per-leaf duplication would catch no new bugs.

Scope (DRY vs test_sinks.py — those cover SharePoint/Asana/Snowflake):
  1. Each push is a no-op when its config block is absent.
  2. Each push rejects missing/empty required-key config.
  3. Each push invokes the right publisher endpoint with the expected body.
  4. Recipient lists (email + calendar attendees) never logged at INFO.
  5. Calendar event description redacts PII from the inputs before the
     event is created.
"""
from __future__ import annotations

import base64
import importlib.util
import logging
import re
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
_AGENT_PATH = HERE.parent / "scripts" / "agent.py"


def _load_agent():
    mod_name = f"family_office_{HERE.parent.name.replace('-', '_')}_agent_comms"
    spec = importlib.util.spec_from_file_location(mod_name, _AGENT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest() -> dict:
    return {
        "artifact_id": "artifact:cpa-tax-package-checklist-deadbeef0000",
        "skill": "cpa-tax-package-checklist",
        "pillar": "complexity-management",
        "artifact_name": "CPA Tax Package Checklist",
        "artifact_version": 1,
        "created_at": "2026-04-20T16:00:00+00:00",
        "content_hash": "deadbeef" * 8,
        "out_dir": "/tmp/irrelevant",
    }


def _answers() -> dict:
    return {"tax_year": "2026", "cpa_firm": "Johnson & Co."}


def _stub_gateway(monkeypatch, module, captured: list) -> None:
    monkeypatch.setenv("SEREN_API_KEY", "test-key")

    class _StubGateway:
        def __init__(self, **kwargs) -> None:  # noqa: ARG002
            pass

        def call_publisher(self, publisher, method, path, *, body=None):
            captured.append(
                {"publisher": publisher, "method": method, "path": path, "body": body}
            )
            return {"ok": True}

    monkeypatch.setattr(module, "GatewayClient", _StubGateway)


# ── No-op + validation ───────────────────────────────────────────────────

def test_outlook_email_noop_when_config_absent() -> None:
    agent = _load_agent()
    assert agent.push_to_outlook_email(_manifest(), _answers(), config=None) is None
    assert agent.push_to_outlook_email(_manifest(), _answers(), config={}) is None


def test_outlook_email_rejects_empty_to_list() -> None:
    agent = _load_agent()
    with pytest.raises(ValueError, match="outlook_email"):
        agent.push_to_outlook_email(
            _manifest(), _answers(), config={"outlook_email": {"to": []}}
        )


def test_gmail_noop_when_config_absent() -> None:
    agent = _load_agent()
    assert agent.push_to_gmail(_manifest(), _answers(), config=None) is None
    assert agent.push_to_gmail(_manifest(), _answers(), config={}) is None


def test_gmail_rejects_empty_to_list() -> None:
    agent = _load_agent()
    with pytest.raises(ValueError, match="gmail"):
        agent.push_to_gmail(
            _manifest(), _answers(), config={"gmail": {"to": []}}
        )


def test_gcalendar_noop_when_config_absent() -> None:
    agent = _load_agent()
    assert agent.push_to_gcalendar(_manifest(), _answers(), config=None) is None
    assert agent.push_to_gcalendar(_manifest(), _answers(), config={}) is None


def test_gcalendar_rejects_missing_calendar_id() -> None:
    # Non-empty gcalendar block (so the "absent sink" early-return doesn't
    # swallow the call) but without the required calendar_id key.
    agent = _load_agent()
    with pytest.raises(ValueError, match="calendar_id"):
        agent.push_to_gcalendar(
            _manifest(),
            _answers(),
            config={"gcalendar": {"attendees": ["advisor@example.com"]}},
        )


# ── Happy path with stubbed transport ───────────────────────────────────

def test_outlook_email_calls_sendmail_with_expected_shape(monkeypatch) -> None:
    agent = _load_agent()
    calls: list[dict] = []
    _stub_gateway(monkeypatch, agent, calls)

    result = agent.push_to_outlook_email(
        _manifest(),
        _answers(),
        config={
            "outlook_email": {
                "to": ["cpa@example.com", "ops@example.com"],
                "cc": ["counsel@example.com"],
                "subject_prefix": "[Seren] ",
            }
        },
    )
    assert result is not None
    assert result["publisher"] == "microsoft-outlook"
    assert len(calls) == 1
    call = calls[0]
    assert call["publisher"] == "microsoft-outlook"
    assert call["method"] == "POST"
    assert call["path"] == "/me/sendMail"
    msg = call["body"]["message"]
    assert "CPA Tax Package Checklist" in msg["subject"]
    assert msg["subject"].startswith("[Seren] ")
    assert msg["body"]["contentType"] == "Text"
    # Body references artifact_id (safe identifier) but NOT raw answers.
    assert "artifact:cpa-tax-package-checklist" in msg["body"]["content"]
    assert "Johnson & Co." not in msg["body"]["content"]
    # Recipients + CCs translated into Graph shape.
    to_addrs = [r["emailAddress"]["address"] for r in msg["toRecipients"]]
    cc_addrs = [r["emailAddress"]["address"] for r in msg["ccRecipients"]]
    assert to_addrs == ["cpa@example.com", "ops@example.com"]
    assert cc_addrs == ["counsel@example.com"]
    assert call["body"]["saveToSentItems"] == "true"


def test_gmail_calls_send_with_base64url_raw_body(monkeypatch) -> None:
    agent = _load_agent()
    calls: list[dict] = []
    _stub_gateway(monkeypatch, agent, calls)

    agent.push_to_gmail(
        _manifest(),
        _answers(),
        config={
            "gmail": {
                "to": ["cpa@example.com"],
                "cc": ["counsel@example.com"],
                "subject_prefix": "[Seren] ",
            }
        },
    )
    assert len(calls) == 1
    call = calls[0]
    assert call["publisher"] == "gmail"
    assert call["method"] == "POST"
    assert call["path"] == "/users/me/messages/send"
    raw = call["body"]["raw"]
    # Must be urlsafe-base64 without padding.
    assert re.fullmatch(r"[A-Za-z0-9\-_]+", raw), "raw must be urlsafe-b64 w/o padding"
    # Reconstruct and verify headers.
    pad = "=" * ((4 - len(raw) % 4) % 4)
    decoded = base64.urlsafe_b64decode(raw + pad).decode("utf-8")
    assert "To: cpa@example.com" in decoded
    assert "Cc: counsel@example.com" in decoded
    assert "Subject: [Seren] CPA Tax Package Checklist" in decoded
    assert "MIME-Version: 1.0" in decoded


def test_gcalendar_creates_event_with_attendees_and_redacted_description(
    monkeypatch,
) -> None:
    agent = _load_agent()
    calls: list[dict] = []
    _stub_gateway(monkeypatch, agent, calls)

    answers_with_pii = dict(_answers())
    answers_with_pii["principal_ssn"] = "123-45-6789"
    answers_with_pii["trust_ein"] = "12-3456789"

    agent.push_to_gcalendar(
        _manifest(),
        answers_with_pii,
        config={
            "gcalendar": {
                "calendar_id": "primary",
                "duration_minutes": 45,
                "attendees": ["advisor@example.com"],
                "days_out": 3,
            }
        },
    )
    assert len(calls) == 1
    call = calls[0]
    assert call["publisher"] == "google-calendar"
    assert call["method"] == "POST"
    assert call["path"] == "/calendars/primary/events"
    event = call["body"]
    assert event["summary"] == "Review: CPA Tax Package Checklist"
    # start + end present, and end > start (defensive sanity check).
    assert "dateTime" in event["start"] and "dateTime" in event["end"]
    assert event["end"]["dateTime"] > event["start"]["dateTime"]
    # Attendees mapped.
    assert event["attendees"] == [{"email": "advisor@example.com"}]
    # Description must NOT carry raw PII.
    description = event["description"]
    assert "123-45-6789" not in description
    assert "12-3456789" not in description
    assert "Johnson & Co." not in description  # answers not dumped into event body


# ── Logging hygiene ─────────────────────────────────────────────────────

def test_outlook_email_never_logs_recipients_at_info(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    agent = _load_agent()
    calls: list[dict] = []
    _stub_gateway(monkeypatch, agent, calls)

    with caplog.at_level(logging.INFO, logger=f"family_office.{agent.SKILL_NAME}"):
        agent.push_to_outlook_email(
            _manifest(),
            _answers(),
            config={
                "outlook_email": {
                    "to": ["cpa@secret-firm.example"],
                    "cc": ["counsel@secret-firm.example"],
                }
            },
        )
    joined = "\n".join(r.getMessage() for r in caplog.records)
    # Counts allowed; cleartext addresses are PII and must not appear.
    assert "cpa@secret-firm.example" not in joined
    assert "counsel@secret-firm.example" not in joined
    assert "to_count=1" in joined
    assert "cc_count=1" in joined


def test_gcalendar_never_logs_calendar_id_at_info(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    agent = _load_agent()
    calls: list[dict] = []
    _stub_gateway(monkeypatch, agent, calls)

    with caplog.at_level(logging.INFO, logger=f"family_office.{agent.SKILL_NAME}"):
        agent.push_to_gcalendar(
            _manifest(),
            _answers(),
            config={
                "gcalendar": {
                    "calendar_id": "secret-family-cal@group.calendar.google.com",
                    "attendees": ["advisor@example.com"],
                }
            },
        )
    joined = "\n".join(r.getMessage() for r in caplog.records)
    # Calendar IDs leak the family's identity in some GCal setups.
    assert "secret-family-cal" not in joined
    assert "attendees_count=1" in joined
