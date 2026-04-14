from __future__ import annotations

from common import footer_missing_placeholders, utc_now


def _default_body_template(program_name: str) -> str:
    return (
        "Hi {name},\n\n"
        f"I wanted to share {program_name}. The team has been shipping quality work and "
        "I think the fit with what you're building is real.\n\n"
        "If you'd like to take a look, my link is here: {partner_link}\n"
        "Happy to answer any questions.\n\n"
        "---\n"
        "{sender_identity}\n"
        "{sender_address}\n"
        "Unsubscribe: {unsubscribe_link}\n"
    )


def draft_pitch(
    *,
    config: dict,
    program: dict,
    run_id: str,
) -> dict:
    voice_notes = str(config["inputs"].get("voice_notes", "")).strip()
    subject = f"Thought of you for {program['program_name']}"
    body = _default_body_template(program["program_name"])

    missing = footer_missing_placeholders(body)
    if missing:
        return {
            "status": "error",
            "error_code": "draft_missing_placeholders",
            "missing_placeholders": missing,
            "message": (
                "Draft output is missing required placeholder tokens. "
                "Re-run draft with tighter voice notes."
            ),
        }

    return {
        "status": "ok",
        "draft": {
            "run_id": run_id,
            "program_slug": program["program_slug"],
            "subject": subject,
            "body_template": body,
            "model_used": "seren-models:claude-opus-4-6",
            "approved_at": None,
            "approved_by": None,
        },
        "voice_notes_length": len(voice_notes),
        "drafted_at": utc_now(),
    }


def await_approval(*, config: dict, draft: dict) -> dict:
    inputs = config["inputs"]
    approve_draft = bool(inputs.get("approve_draft"))
    json_output = bool(inputs.get("json_output"))

    if approve_draft and not json_output:
        return {
            "status": "error",
            "error_code": "approve_draft_without_json_output",
            "message": (
                "approve_draft=true requires json_output=true. "
                "The approval gate cannot be auto-bypassed in human CLI mode."
            ),
        }

    if approve_draft:
        return {
            "status": "approved",
            "auto": True,
            "approved_at": utc_now(),
            "approved_by": "agent_auto_approval",
            "draft_id": draft["run_id"],
        }

    return {
        "status": "pending_approval",
        "auto": False,
        "message": (
            "Review the draft subject, body, recipient count, and sample merge. "
            "Re-run `send` with approve_draft=true (and json_output=true for agent mode) "
            "once you're satisfied."
        ),
        "draft_id": draft["run_id"],
    }
